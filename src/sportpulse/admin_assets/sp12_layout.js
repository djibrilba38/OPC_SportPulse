/* Navigation RH allégée, sans endpoint ni lien replay dans l'interface. */
(() => {
  'use strict';
  window.addEventListener('DOMContentLoaded',()=>{
    const dash=document.getElementById('dashboard');if(!dash)return;
    dash.querySelectorAll('a[href="/admin/replay"]').forEach(a=>{
      const parent=a.closest('p');if(parent&&parent.textContent.trim()===a.textContent.trim())parent.remove();else a.remove();
    });
    let details=document.getElementById('sp12-advanced');
    if(!details){
      details=document.createElement('details');details.className='panel';details.id='sp12-advanced';
      const summary=document.createElement('summary');summary.textContent='Historique et journal · 5 derniers événements';details.append(summary);
      const anchor=dash.querySelector('footer');anchor?.before(details);
    }else{
      const summary=details.querySelector('summary');if(summary)summary.textContent='Historique et journal · 5 derniers événements';
    }
    const history=document.getElementById('history-rows')?.closest('section');
    const audit=document.getElementById('audit-panel');
    if(history&&!details.contains(history))details.append(history);
    if(audit&&!details.contains(audit))details.append(audit);
    const reportLink=dash.querySelector('a[href="/admin/reporting"]');
    if(reportLink && !details.contains(reportLink)){
      const p=reportLink.closest('p');
      if(p&&p.textContent.trim()===reportLink.textContent.trim())details.append(p);else details.append(reportLink);
    }
    const source=document.getElementById('source');
    if(source&&Array.from(source.options).some(x=>x.value==='postgres'))source.value='postgres';
  });
})();
