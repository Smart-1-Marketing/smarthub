/* One chip, one URL, built in one place.

   A recipe chip is a front door to /ask-smarthub and never a second copy of
   the chat: every placement -- the reporting hub, Client 360, the pacing
   board, the optimization page -- links to the same page with the same query
   string, and the page asks the question once on arrival. Six placements each
   building that URL by hand is six chances to spell `recipe` or `client`
   slightly differently and land on a page that opens empty while reporting
   nothing wrong.

   A chip carries data-recipe, and optionally data-client, data-period and
   data-placement. A module page mounted under a prefix can use this too: the
   URL is root-absolute on purpose, because a relative one resolves under the
   module's own mount and 404s -- the trap CLAUDE.md names first. */
(function () {
  'use strict';

  function url(chip) {
    var p = new URLSearchParams();
    p.set('recipe', chip.getAttribute('data-recipe') || '');
    var client = chip.getAttribute('data-client');
    if (client) p.set('client', client);
    var period = chip.getAttribute('data-period');
    if (period) p.set('period', period);
    var placement = chip.getAttribute('data-placement');
    if (placement) p.set('placement', placement);
    var path = chip.getAttribute('data-context-path') || window.location.pathname;
    if (path) p.set('context_path', path);
    return '/ask-smarthub?' + p.toString();
  }

  function wire(root) {
    var chips = (root || document).querySelectorAll('[data-recipe]');
    Array.prototype.forEach.call(chips, function (chip) {
      if (chip.getAttribute('data-recipe-wired')) return;
      chip.setAttribute('data-recipe-wired', '1');
      if (chip.tagName === 'A') {
        chip.setAttribute('href', url(chip));
        return;
      }
      chip.addEventListener('click', function (event) {
        event.preventDefault();
        window.location.href = url(chip);
      });
    });
  }

  window.askRecipes = { url: url, wire: wire };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { wire(document); });
  } else {
    wire(document);
  }
})();
