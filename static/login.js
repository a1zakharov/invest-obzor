'use strict';
const loginForm=document.querySelector('#login-form');
loginForm.addEventListener('submit',async event=>{
  event.preventDefault();
  const button=loginForm.querySelector('button'),error=document.querySelector('#login-error');
  error.textContent='';button.disabled=true;
  try{
    const password=loginForm.elements.password.value;
    loginForm.elements.password.value='';
    const response=await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:loginForm.elements.username.value,password})});
    const result=await response.json();
    if(!response.ok)throw new Error(result.error||'Не удалось войти');
    location.replace('/');
  }catch(reason){error.textContent=reason.message;loginForm.elements.password.focus();}
  finally{button.disabled=false;}
});
fetch('/api/auth/status').then(r=>r.json()).then(s=>{if(s.authenticated)location.replace('/');}).catch(()=>{});
