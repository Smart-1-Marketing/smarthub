<?php
/**
 * hub/wordpress_plugin/smart-1-hub.php -- run against enough of WordPress to
 * call it. Driven by test_wordpress_schema.py; prints one JSON object of
 * label => bool on stdout.
 *
 * This exists because the rest of that file's assertions about the plugin are
 * greps, and a grep over PHP proves the source says something rather than that
 * the code does it. The escape in `s1hub_print_schema()` is one line standing
 * between a client's every page and a script block that ends early -- and the
 * first version of this harness went red on it for a reason that was the
 * harness's own, which is the argument for having it at all.
 *
 * The stubs are the smallest set the plugin actually touches. They are not a
 * model of WordPress and are not meant to be: what is asserted is the plugin's
 * own logic, and anything it delegates to core is core's to get right.
 */
// Enough WordPress to load the plugin and call the two pure functions in it.
define('ABSPATH', __DIR__ . '/');
$GLOBALS['s1_actions'] = array();
function add_action($hook, $fn, $prio = 10, $args = 1) { $GLOBALS['s1_actions'][$hook][] = $fn; }
function register_post_meta($t, $k, $a) { $GLOBALS['s1_meta'][$t] = $k; }
function get_post_types($a, $b) { return array('post' => 'post', 'page' => 'page', 'attachment' => 'attachment'); }
function is_singular() { return $GLOBALS['s1_singular']; }
function get_queried_object_id() { return $GLOBALS['s1_qid']; }
function get_post_meta($id, $k, $s) { return isset($GLOBALS['s1_store'][$id]) ? $GLOBALS['s1_store'][$id] : ''; }
function url_to_postid($u) { return isset($GLOBALS['s1_urls'][$u]) ? $GLOBALS['s1_urls'][$u] : 0; }
function get_option($k) { return isset($GLOBALS['s1_options'][$k]) ? $GLOBALS['s1_options'][$k] : 0; }
function untrailingslashit($s) { return rtrim($s, '/'); }
function home_url($p = '/') { return 'https://example.test' . $p; }
function get_post($id) { return isset($GLOBALS['s1_posts'][$id]) ? (object) $GLOBALS['s1_posts'][$id] : null; }
function get_post_type_object($t) { return (object) array('show_in_rest' => true, 'rest_base' => $t . 's'); }
function get_the_title($id) { return 'T' . $id; }
function get_permalink($id) { return 'https://example.test/p/' . $id; }
function current_user_can($c) { return true; }
function register_rest_route($ns, $route, $args) { $GLOBALS['s1_routes'][] = $ns . $route; }
class WP_REST_Request { public $p = array();
    function __construct($p = array()) { $this->p = $p; }
    function get_param($k) { return isset($this->p[$k]) ? $this->p[$k] : null; } }

require __DIR__ . '/hub/wordpress_plugin/smart-1-hub.php';

// Fire the hooks WordPress would fire, or nothing in the plugin has run.
$GLOBALS['s1_routes'] = array();
$GLOBALS['s1_meta'] = array();
foreach (array('init', 'rest_api_init') as $hook) {
    foreach ($GLOBALS['s1_actions'][$hook] as $fn) { $fn(); }
}

$out = array();

// --- sanitize -------------------------------------------------------------
$block = '{"@context":"https://schema.org","@graph":[{"@type":"X"}]}';
$out['keeps_valid_json_verbatim'] = (s1hub_sanitize($block) === $block);
$out['rejects_broken_json'] = (s1hub_sanitize('{"a":') === '');
$out['rejects_a_bare_string'] = (s1hub_sanitize('"hello"') === '');
$out['clearing_is_allowed'] = (s1hub_sanitize('') === '');
$out['rejects_something_enormous'] = (s1hub_sanitize('[' . str_repeat('1,', 120000) . '1]') === '');

// --- the printed block ----------------------------------------------------
$GLOBALS['s1_singular'] = true;
$GLOBALS['s1_qid'] = 42;
$GLOBALS['s1_store'] = array(42 => '{"a":"we fix roofs </script><img src=x> and gutters"}');
ob_start(); s1hub_print_schema(); $head = ob_get_clean();

$out['prints_a_block'] = (strpos($head, 'application/ld+json') !== false);
$out['no_raw_script_close_survives'] = (strpos($head, '</script><img') === false);
// The one <\/script> in the output is the block's own closing tag.
$out['closes_exactly_once'] = (substr_count($head, '</script>') === 1);
// And it is still the same data: unescape and the JSON must decode to what
// went in. An escape that changed the meaning would be a different bug.
preg_match('~<script type="application/ld\+json">\n(.*)\n</script>~s', $head, $m);
$decoded = json_decode($m[1], true);
$out['the_data_is_unchanged'] =
    is_array($decoded) && $decoded['a'] === 'we fix roofs </script><img src=x> and gutters';

$GLOBALS['s1_store'] = array(42 => "{\"a\":\"line\xE2\x80\xA8break\"}");
ob_start(); s1hub_print_schema(); $head2 = ob_get_clean();
$esc = chr(92) . 'u2028';
$out['u2028_is_escaped'] = (strpos($head2, "\xE2\x80\xA8") === false
                            && strpos($head2, $esc) !== false);

$GLOBALS['s1_store'] = array(42 => '   ');
ob_start(); s1hub_print_schema(); $blank = ob_get_clean();
$out['an_empty_block_prints_nothing'] = ($blank === '');

$GLOBALS['s1_singular'] = false;
$GLOBALS['s1_store'] = array(42 => $block);
ob_start(); s1hub_print_schema(); $arch = ob_get_clean();
$out['nothing_is_printed_on_an_archive'] = ($arch === '');

// --- the resolver ---------------------------------------------------------
$GLOBALS['s1_urls'] = array('https://example.test/roofing/' => 9);
$GLOBALS['s1_posts'] = array(9 => array('post_type' => 'page', 'post_status' => 'publish'));
$r = s1hub_rest_resolve(new WP_REST_Request(array('url' => 'https://example.test/roofing/')));
$out['resolves_a_page'] = ($r['id'] === 9 && $r['rest_base'] === 'pages' && $r['registered'] === true);

$r = s1hub_rest_resolve(new WP_REST_Request(array('url' => 'https://example.test/category/news/')));
$out['an_archive_resolves_to_nothing_with_a_reason'] = ($r['id'] === 0 && !empty($r['reason']));

$GLOBALS['s1_options'] = array('page_on_front' => 5);
$GLOBALS['s1_posts'][5] = array('post_type' => 'page', 'post_status' => 'publish');
$r = s1hub_rest_resolve(new WP_REST_Request(array('url' => 'https://example.test/')));
$out['a_static_front_page_resolves'] = ($r['id'] === 5);

$r = s1hub_rest_resolve(new WP_REST_Request(array('url' => '')));
$out['an_empty_url_is_refused'] = ($r['id'] === 0);

// --- registration ---------------------------------------------------------
$out['attachments_are_left_out'] = !in_array('attachment', s1hub_post_types(), true);
$out['the_meta_key_is_the_one_the_hub_writes'] = (S1HUB_META === '_s1hub_schema');
$out['the_meta_is_registered_for_posts_and_pages'] =
    (array_keys($GLOBALS['s1_meta']) === array('post', 'page'));
$out['both_routes_are_registered'] = (count($GLOBALS['s1_routes']) === 2);

echo json_encode($out);
