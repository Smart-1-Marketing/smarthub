/**
 * Serving this app from under a URL prefix.
 *
 * Every page here links and fetches from the site root — `fetch('/api/render')`,
 * `href="/projects"`. That is correct for a standalone service on its own
 * domain, which is how this app started. Inside the Smart 1 Hub it is mounted
 * at /tools/display-ads and proxied, so those root-absolute URLs leave the
 * mount: the browser asks the Hub for /api/render, the Hub has no such route,
 * and the page loads looking perfect while every button does nothing. That
 * failure is worse than a page that refuses to load, because nothing says why.
 *
 * The fix goes here rather than in the proxy because only this app knows which
 * responses are HTML, and rewriting HTML with regular expressions in a proxy
 * breaks the first time someone writes a URL in an unexpected shape. The
 * prefix arrives as X-Forwarded-Prefix; with no header the base is empty and
 * every page behaves exactly as it did standalone.
 */
import type { IncomingMessage } from 'node:http';

/** The mount prefix for this request: '/tools/display-ads' or ''. */
export function basePrefix(req: IncomingMessage): string {
  const raw = req.headers['x-forwarded-prefix'];
  const value = (Array.isArray(raw) ? raw[0] : raw) ?? '';
  // Only a simple absolute path is accepted. The header comes from our own
  // proxy, but a header is still caller-controlled input and this value is
  // interpolated into a page — anything with a quote, angle bracket or scheme
  // in it is refused rather than escaped, because nothing legitimate needs it.
  if (!/^\/[A-Za-z0-9/_-]*$/.test(value)) return '';
  return value.replace(/\/$/, '');
}

/**
 * Inject the base-path shim into a page.
 *
 * It patches fetch and XMLHttpRequest, and rewrites href/src/action attributes
 * as they appear — including on markup added later, which these pages do a lot
 * of. Patching at that level rather than editing each call site means a fetch
 * added to this app next year is prefixed too, without anyone remembering to.
 */
export function withBase(req: IncomingMessage, html: string): string {
  const base = basePrefix(req);
  if (!base) return html;

  const shim = `<script>(function(){
var B=${JSON.stringify(base)};
window.S1_BASE=B;
function pre(u){return (typeof u==='string'&&u.charAt(0)==='/'&&u.charAt(1)!=='/'&&u.indexOf(B+'/')!==0)?B+u:u;}
var f=window.fetch;
window.fetch=function(i,o){return f.call(this,(typeof i==='string')?pre(i):i,o);};
var xo=XMLHttpRequest.prototype.open;
XMLHttpRequest.prototype.open=function(){arguments[1]=pre(arguments[1]);return xo.apply(this,arguments);};
function fix(r){if(!r||!r.querySelectorAll)return;
 var s='[href^="/"],[src^="/"],[action^="/"]';
 var all=(r.matches&&r.matches(s))?[r]:[];
 all=all.concat(Array.prototype.slice.call(r.querySelectorAll(s)));
 all.forEach(function(el){['href','src','action'].forEach(function(a){
  var v=el.getAttribute(a); if(!v)return; var n=pre(v); if(n!==v)el.setAttribute(a,n);});});}
function boot(){fix(document.body||document.documentElement);
 new MutationObserver(function(ms){ms.forEach(function(m){
  Array.prototype.forEach.call(m.addedNodes,function(n){if(n.nodeType===1)fix(n);});});})
 .observe(document.documentElement,{childList:true,subtree:true});}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot);else boot();
})();</script>`;

  // After <head> so the patches are in place before any page script runs; if a
  // page somehow has no <head>, prepend rather than silently skipping it.
  if (/<head[^>]*>/i.test(html)) {
    return html.replace(/<head[^>]*>/i, (m) => m + shim);
  }
  return shim + html;
}
