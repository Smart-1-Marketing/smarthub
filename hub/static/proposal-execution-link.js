/* Proposal Execution Center entry point for Client 360.

Client 360 builds its proposal table after several async reads, so a static link
in the server template cannot know which rows are uploaded documents. Observe the
finished table and add Execute only beside rows that already expose "To IO" —
that is Client 360's own proof that the row has a PDF/DOCX behind it rather than
a live Hub-page link.
*/
(function(){
  'use strict';
  function wire(){
    document.querySelectorAll('.c360-proposals-table tr[data-pid]').forEach(function(row){
      if(row.querySelector('.pex-execute') || !row.querySelector('.to-io')) return;
      var cell=row.querySelector('td:last-child');
      if(!cell) return;
      var client=row.dataset.pclient || window.CURRENT_CLIENT || '';
      var proposal=row.dataset.pid || '';
      if(!client || !proposal) return;
      var a=document.createElement('a');
      a.className='gbtn pex-execute';
      a.textContent='Execute';
      a.title='Turn this proposal into one batch execution plan';
      a.href='/proposal-execution?client='+encodeURIComponent(client)
        +'&proposal='+encodeURIComponent(proposal);
      cell.insertBefore(a, cell.querySelector('.up-del'));
      cell.insertBefore(document.createTextNode(' '), cell.querySelector('.up-del'));
    });
  }
  var queued=false;
  function queue(){
    if(queued) return; queued=true;
    requestAnimationFrame(function(){queued=false;wire();});
  }
  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',queue);
  else queue();
  new MutationObserver(queue).observe(document.documentElement,{childList:true,subtree:true});
})();
