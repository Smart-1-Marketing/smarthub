<?php
/**
 * Plugin Name:       Smart 1 Hub — structured data
 * Plugin URI:        https://smart1.agency
 * Description:       Lets Smart 1 Marketing write approved schema.org JSON-LD onto this site's pages from the Hub, and prints it in the page head. It also makes the meta description field of an installed SEO plugin writable over the REST API. It publishes nothing, changes no page content, and collects nothing.
 * Version:           1.1.0
 * Requires at least: 5.6
 * Requires PHP:      7.2
 * Author:            Smart 1 Marketing
 * Author URI:        https://smart1marketing.com
 * License:           GPL-2.0-or-later
 *
 * ---------------------------------------------------------------------------
 * Why this file exists
 *
 * WordPress core's REST API can already write a post, a category and an image's
 * alt text, so the Hub does all three with nothing installed here. Schema is
 * the one thing it cannot: Yoast and Rank Math keep their schema fields in
 * postmeta that is not `show_in_rest`, and JSON-LD pasted into post *content*
 * is stripped by `wp_kses` for every user without `unfiltered_html`. The way
 * past that is not a wider credential -- it is one registered meta key that
 * core will then accept over REST, and an output hook that prints it.
 *
 * What it deliberately does NOT do, so its footprint is a decision rather than
 * something to be discovered:
 *
 *   * It never touches post content. The block goes in the head, through
 *     `wp_head`, and the page body is left exactly as the site's own editor
 *     left it.
 *   * It never publishes, schedules or unpublishes anything.
 *   * It adds no admin screen, no menu, no dashboard widget and no notice.
 *   * It sends nothing anywhere. Both REST routes are reads, and both require
 *     a signed-in user who can already edit posts.
 *   * It registers ONE meta key. An earlier draft registered a second for FAQ
 *     markup and it is not here, because the FAQ accordion the Hub produces
 *     carries its own FAQPage JSON-LD inside the block that goes on the page --
 *     writing it again from here would duplicate it, and writing it without
 *     the accordion would be FAQPage markup for questions a visitor cannot
 *     see. A meta key nothing writes is a promise nobody keeps.
 */

if (!defined('ABSPATH')) {
    exit;
}

define('S1HUB_VERSION', '1.1.0');
define('S1HUB_META', '_s1hub_schema');
define('S1HUB_NS', 's1hub/v1');

/**
 * The post types the meta is registered for.
 *
 * Public and already in REST, because a type core will not serve over REST is
 * one the Hub could not write to whatever we registered. Attachments are left
 * out: an attachment page is not a page anybody builds schema for, and the Hub
 * already writes `alt_text` on media through core.
 */
/**
 * The meta description field of whichever SEO plugin is installed.
 *
 * Core can already write a post, its categories and its featured image. The
 * one field on a published blog post it cannot reach is the meta description,
 * because every SEO plugin keeps that in its OWN postmeta and none of them
 * registers it for REST. So the Hub wrote it into the post's Excerpt instead
 * and told the rep to copy it across by hand -- the last step of publishing a
 * blog post that a person still had to do twice.
 *
 * `register_post_meta()` is additive: registering a key another plugin owns
 * exposes THAT key to core's REST API, and the value lands in the field the
 * plugin already reads. Nothing here renders a meta description, and nothing
 * here reads one back out -- the SEO plugin on the site goes on owning it.
 *
 * Only the key of a plugin that is ACTUALLY INSTALLED is registered. Writing
 * `_yoast_wpseo_metadesc` on a site with no Yoast puts a row in the client's
 * postmeta that nothing will ever read, and the Hub would report it as
 * written because the read-back would agree.
 *
 * All in One SEO is deliberately absent rather than missing: it stores its
 * metadata in its own `wp_aioseo_posts` TABLE and not in postmeta, so there is
 * no key to register and this route cannot reach it at all. Named here so the
 * next person to wonder does not have to find that out by trying.
 */
function s1hub_description_keys() {
    $keys = array();
    if (defined('WPSEO_VERSION') || class_exists('WPSEO_Options')) {
        $keys['Yoast SEO'] = '_yoast_wpseo_metadesc';
    }
    if (class_exists('RankMath') || defined('RANK_MATH_VERSION')) {
        $keys['Rank Math'] = 'rank_math_description';
    }
    if (defined('SEOPRESS_VERSION')) {
        $keys['SEOPress'] = '_seopress_titles_desc';
    }
    return $keys;
}

function s1hub_post_types() {
    $types = get_post_types(array('public' => true, 'show_in_rest' => true), 'names');
    unset($types['attachment']);
    return array_values($types);
}

/**
 * Accept the block, or store nothing at all.
 *
 * Stored VERBATIM when it is valid. The Hub reads the value back out of the
 * response to the same request that wrote it and compares -- that read-back is
 * what turns "WordPress answered 200" into "the block is on the site" -- and
 * re-encoding here would change the bytes and break that comparison for a
 * block that was perfectly good.
 *
 * Anything that is not valid JSON is stored as the empty string rather than
 * kept, so the read-back disagrees and the Hub can say the site rejected it.
 * Silently keeping malformed JSON would put a broken script tag in the head of
 * a client's every page.
 */
function s1hub_sanitize($value) {
    $value = is_string($value) ? trim($value) : '';
    if ($value === '') {
        return '';
    }
    if (strlen($value) > 200000) {
        return '';
    }
    $decoded = json_decode($value, true);
    if (json_last_error() !== JSON_ERROR_NONE || !is_array($decoded)) {
        return '';
    }
    return $value;
}

/**
 * Registered on `init` at priority 20 so custom post types registered at the
 * default priority already exist by the time this runs.
 */
function s1hub_register_meta() {
    $can_edit = function ($allowed, $meta_key, $post_id) {
        return current_user_can('edit_post', $post_id);
    };
    $description_keys = array_values(s1hub_description_keys());
    foreach (s1hub_post_types() as $type) {
        register_post_meta($type, S1HUB_META, array(
            'single'            => true,
            'type'              => 'string',
            'default'           => '',
            'show_in_rest'      => true,
            'sanitize_callback' => 's1hub_sanitize',
            'auth_callback'     => $can_edit,
        ));
        foreach ($description_keys as $key) {
            // No sanitize_callback of our own: this is the SEO plugin's field
            // and its own filters still run on the way in. What we must not do
            // is impose a length or strip characters it would have kept --
            // that would silently edit copy the client approved.
            register_post_meta($type, $key, array(
                'single'        => true,
                'type'          => 'string',
                'default'       => '',
                'show_in_rest'  => true,
                'auth_callback' => $can_edit,
            ));
        }
    }
}
add_action('init', 's1hub_register_meta', 20);

/**
 * Print the block in the head of the page it belongs to.
 *
 * `<` is escaped to its `<` JSON escape, which decodes to the same
 * character and is invisible to an HTML parser. Every `<` in valid JSON is
 * inside a string literal, so this cannot change what the block means -- and
 * without it a `</script>` anywhere in the data ends the block early and the
 * rest of it is parsed as markup. U+2028 and U+2029 go with it: both are legal
 * inside a JSON string and both are line terminators to a JavaScript parser,
 * so either one unescaped is a syntax error that costs the whole block.
 */
function s1hub_print_schema() {
    if (!is_singular()) {
        return;
    }
    $post_id = get_queried_object_id();
    if (!$post_id) {
        return;
    }
    $block = get_post_meta($post_id, S1HUB_META, true);
    if (!is_string($block) || trim($block) === '') {
        return;
    }
    $safe = str_replace(
        array('<', "\xE2\x80\xA8", "\xE2\x80\xA9"),
        array('\\u003c', '\\u2028', '\\u2029'),
        $block
    );
    echo "\n<!-- Smart 1 Hub structured data -->\n";
    echo '<script type="application/ld+json">' . "\n";
    echo $safe . "\n";
    echo '</script>' . "\n";
}
add_action('wp_head', 's1hub_print_schema', 20);

/**
 * Which SEO plugins are also emitting schema on this site.
 *
 * Reported, never acted on. Two `@graph` blocks on one page is legal and is
 * also how a page comes to describe two different Organizations, and which of
 * them is right is a judgement about this client's site rather than something
 * a plugin gets to decide. The Hub prints this beside every result so somebody
 * can look.
 */
function s1hub_seo_plugins() {
    $found = array();
    if (defined('WPSEO_VERSION') || class_exists('WPSEO_Options')) {
        $found[] = 'Yoast SEO';
    }
    if (class_exists('RankMath') || defined('RANK_MATH_VERSION')) {
        $found[] = 'Rank Math';
    }
    if (defined('AIOSEO_VERSION')) {
        $found[] = 'All in One SEO';
    }
    if (defined('SEOPRESS_VERSION')) {
        $found[] = 'SEOPress';
    }
    return $found;
}

function s1hub_rest_status() {
    $keys = s1hub_description_keys();
    return array(
        'plugin'           => 'smart-1-hub',
        'version'          => S1HUB_VERSION,
        'meta_key'         => S1HUB_META,
        'post_types'       => s1hub_post_types(),
        'seo_plugins'      => s1hub_seo_plugins(),
        // Which SEO plugin's description field this site will accept a write
        // to, and under what key. Empty is a real answer -- the Hub falls back
        // to the Excerpt and says so, rather than writing a key nothing reads.
        'description_key'  => $keys ? reset($keys) : '',
        'description_by'   => $keys ? key($keys) : '',
        'must_use'         => defined('WPMU_PLUGIN_DIR')
                              && strpos(__FILE__, WPMU_PLUGIN_DIR) === 0,
    );
}

/**
 * Which post this URL is, answered by WordPress rather than guessed at.
 *
 * `url_to_postid()` is core's own resolver: it knows the permalink structure,
 * page hierarchy and custom post type rewrite rules, none of which can be
 * worked out from the outside. The Hub used to have to search by slug, and a
 * slug is not unique across post types or across a page hierarchy -- filing
 * one page's schema onto another page with the same slug is exactly the silent
 * wrong answer this route exists to remove.
 *
 * `url_to_postid()` answers 0 for the site's front page, so a static front
 * page is resolved from the option instead. Left out, the one page every site
 * has would be the one page the Hub could not write to.
 */
function s1hub_rest_resolve(WP_REST_Request $request) {
    $url = trim((string) $request->get_param('url'));
    if ($url === '') {
        return array('id' => 0, 'reason' => 'No URL was given.');
    }

    $post_id = url_to_postid($url);
    if (!$post_id) {
        $front = (int) get_option('page_on_front');
        if ($front && untrailingslashit($url) === untrailingslashit(home_url('/'))) {
            $post_id = $front;
        }
    }
    if (!$post_id) {
        return array(
            'id'     => 0,
            'reason' => 'WordPress does not resolve this address to a single '
                        . 'post or page. Archives, category listings, search '
                        . 'results and a blog-index home page have no post to '
                        . 'attach schema to.',
        );
    }

    $post = get_post($post_id);
    if (!$post) {
        return array('id' => 0, 'reason' => 'That post no longer exists.');
    }

    $type_object = get_post_type_object($post->post_type);
    $rest_base = '';
    if ($type_object && !empty($type_object->show_in_rest)) {
        $rest_base = !empty($type_object->rest_base)
            ? $type_object->rest_base
            : $post->post_type;
    }

    return array(
        'id'         => (int) $post_id,
        'type'       => $post->post_type,
        'rest_base'  => $rest_base,
        'status'     => $post->post_status,
        'title'      => get_the_title($post_id),
        'link'       => get_permalink($post_id),
        'registered' => in_array($post->post_type, s1hub_post_types(), true),
    );
}

/**
 * Both routes are reads and both require a user who can already edit posts.
 * A resolver open to the world is a site-structure disclosure nobody asked
 * for, and the status route names the plugins installed on the site.
 */
add_action('rest_api_init', function () {
    $can_edit = function () {
        return current_user_can('edit_posts');
    };
    register_rest_route(S1HUB_NS, '/status', array(
        'methods'             => 'GET',
        'callback'            => 's1hub_rest_status',
        'permission_callback' => $can_edit,
    ));
    register_rest_route(S1HUB_NS, '/resolve', array(
        'methods'             => 'GET',
        'callback'            => 's1hub_rest_resolve',
        'permission_callback' => $can_edit,
        'args'                => array(
            'url' => array('required' => true, 'type' => 'string'),
        ),
    ));
});

/**
 * Deactivating leaves the blocks in postmeta and stops printing them.
 *
 * Nothing is deleted on deactivation or uninstall, deliberately: the schema is
 * the client's own approved content, and a plugin that empties a database
 * table because somebody toggled it off is a plugin nobody trusts. Removing it
 * for good is Tools -> the Hub's disconnect, or deleting the meta by hand.
 */
