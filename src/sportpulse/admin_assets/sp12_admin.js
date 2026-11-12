/* RH : générateur de code à usage unique, intégré à l'accueil administrateur */
(() => {
  'use strict';
  const $=id=>document.getElementById(id);
  window.addEventListener('DOMContentLoaded',()=>{
    const form=$('sp12-code-form');if(!form)return;
    form.addEventListener('submit',async e=>{
      e.preventDefault();
      $('sp12-code-error').textContent='';
      try {
        const token=await (await fetch('/admin/api/csrf',{method:'POST',credentials:'same-origin'})).json();
        const body={employee_id:$('sp12-employee').value.trim(),activity_ref:$('sp12-activity').value.trim(),
          kind:$('sp12-kind').value};
        const r=await fetch('/admin/api/proofs/issue', {method:'POST',credentials:'same-origin',
          headers:{'Content-Type':'application/json','X-CSRF-Token':token.csrf_token},body:JSON.stringify(body)});
        const json=await r.json();if(!r.ok)throw Error(json.detail||`HTTP ${r.status}`);
        $('sp12-generated-code').textContent=json.code;
        $('sp12-qr-image').src=json.qr_data_url;
        $('sp12-proof-result').hidden=false;
        $('sp12-validity').textContent='Valable 5 minutes pour cet élément et ce salarié, une seule utilisation.';
      }catch(err){$('sp12-code-error').textContent=err.message;}
    });
  });
})();
