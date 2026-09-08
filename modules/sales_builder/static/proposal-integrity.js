/* Pure planning helpers shared by the wizard and its regression tests. */
(function(root){
 'use strict';
 function allocate(items,budget,minimum){
  budget=Number(budget);if(!Number.isFinite(budget))return items.map(()=>0);
  budget=Math.max(0,Math.round(budget*100));
  const active=items.map((item,index)=>({item,index,weight:Math.max(0,Number(item.dollars)||Number(item.pctSeed)||1),floor:Math.ceil(minimum(item)*100)}));
  // Preserve the strongest channels when the budget cannot support all floors.
  while(active.length && active.reduce((s,r)=>s+r.floor,0)>budget){
   const weakest=active.reduce((a,b)=>a.weight<=b.weight?a:b);active.splice(active.indexOf(weakest),1);
  }
  const result=items.map(()=>0);if(!active.length)return result;
  let remaining=budget-active.reduce((s,r)=>s+r.floor,0);
  const total=active.reduce((s,r)=>s+r.weight,0);
  const rows=active.map(r=>({...r,extra:Math.floor(remaining*r.weight/total),fraction:remaining*r.weight/total%1}));
  let cents=remaining-rows.reduce((s,r)=>s+r.extra,0);
  [...rows].sort((a,b)=>b.fraction-a.fraction).forEach(r=>{if(cents>0){r.extra++;cents--;}});
  rows.forEach(r=>result[r.index]=(r.floor+r.extra)/100);return result;
 }
 function capture(container,answers){
  container.querySelectorAll('[data-conv-field]').forEach(el=>{answers[el.dataset.convField]=el.value;});
  return answers;
 }
 const api={allocate,capture};
 if(typeof module!=='undefined')module.exports=api;root.ProposalIntegrity=api;
})(typeof window!=='undefined'?window:globalThis);
