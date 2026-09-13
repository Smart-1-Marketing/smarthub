(() => {
  const page=JSON.parse(document.getElementById('industry-page').textContent), form=document.getElementById('lead-form');
  document.getElementById('find').onclick=()=>{document.getElementById('opportunities').hidden=false;document.getElementById('find').hidden=true;};
  form.onsubmit=async e=>{
    e.preventDefault(); if(page.preview || !form.reportValidity())return;
    const message=document.getElementById('lead-status'), button=form.querySelector('button');
    button.disabled=true; message.textContent='Saving your request…';
    const query=new URLSearchParams(location.search), meta={page_id:page.id,industry_id:page.industry_id,referrer:query.get('parent_referrer') || document.referrer};
    ['utm_source','utm_medium','utm_campaign','utm_content','utm_term'].forEach(k=>meta[k]=query.get(k)||'');
    try{
      const response=await fetch('/api/leads/capture',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({source:'landing',page:'industry-'+page.id,fields:Object.fromEntries(new FormData(form)),meta})});
      const data=await response.json(); if(!response.ok || !data.ok)throw new Error(data.error || 'Your request could not be saved. Please retry.');
      message.textContent='Your request is saved. View or print your Storm Opportunity Report.';
      const link=document.createElement('a');link.href='/industry/p/'+page.id+'/report';link.textContent='Open my report';link.target='_blank';link.rel='noopener';message.append(document.createElement('br'),link);
    }catch(error){message.textContent=error.message;button.disabled=false;}
  };
})();
