(() => {
  'use strict';
  window.StudioHandheld=ctx=>{
    const $=s=>document.querySelector(s),key='clipmaker-handheld-v1';
    let mode=localStorage.getItem(key)||'auto',drawer='',padTimer=null,lastButtons=[],lastNav=0;
    const control=$('#studio-handheld-mode'),editor=$('#studio-editor');
    function show(value){drawer=value;editor.dataset.drawer=drawer;for(const name of ['sources','effects','inspector'])$('#handheld-'+name).setAttribute('aria-pressed',drawer===name);if(drawer==='effects')$('#studio-effects-tab').click();else if(drawer==='sources')$('#studio-sources-tab').click();}
    function panel(value){show(drawer===value?'':value);}
    function update(){const on=mode==='on'||mode==='auto'&&(innerWidth<=1280&&innerHeight<=800||matchMedia('(pointer:coarse)').matches&&innerWidth<=1920);document.body.classList.toggle('handheld',on);$('#studio-handheld-tools').hidden=!on;control.value=mode;ctx.layout();}
    control.onchange=()=>{mode=control.value;localStorage.setItem(key,mode);update();};
    for(const name of ['sources','effects','inspector'])$('#handheld-'+name).onclick=()=>panel(name);
    $('#handheld-audio').onclick=()=>{const on=editor.dataset.trackFocus!=='audio';editor.dataset.trackFocus=on?'audio':'';$('#handheld-audio').setAttribute('aria-pressed',on);$('#studio-timeline-scroll').scrollTop=0;ctx.layout();};
    document.querySelectorAll('[data-close-drawer]').forEach(b=>b.onclick=()=>show(''));
    for(const [id,step]of [['back',-5],['forward',5]])$('#handheld-'+id).onclick=()=>ctx.seek(ctx.time()+step);
    $('#handheld-zoom-in').onclick=()=>zoom(1.35);$('#handheld-zoom-out').onclick=()=>zoom(1/1.35);
    function zoom(factor){const slider=$('#studio-zoom');slider.value=Math.max(.1,Math.min(100,Number(slider.value)*factor));slider.dispatchEvent(new Event('input',{bubbles:true}));}
    function nav(direction){const modal=document.querySelector('dialog[open]'),scope=modal||document;const items=[...scope.querySelectorAll('button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),[tabindex="0"]')].filter(el=>el.getClientRects().length&&!el.closest('[hidden]'));if(!items.length)return;const index=items.indexOf(document.activeElement);items[(index+direction+items.length)%items.length].focus();}
    function poll(){
      if(!document.body.classList.contains('handheld')||!document.hasFocus()){lastButtons=[];return;}
      const pad=[...(navigator.getGamepads?.()||[])].find(p=>p?.mapping==='standard');if(!pad){lastButtons=[];return;}
      const pressed=pad.buttons.map(b=>b.pressed),tap=i=>pressed[i]&&!lastButtons[i];
      if(!lastButtons.length){lastButtons=pressed;return;}
      const modal=document.querySelector('dialog[open]'),now=performance.now();
      if(now-lastNav>220){if(pressed[13]||pressed[15]||pad.axes[1]>.6){nav(1);lastNav=now;}else if(pressed[12]||pressed[14]||pad.axes[1]<-.6){nav(-1);lastNav=now;}}
      if(tap(0)&&['BUTTON','INPUT'].includes(document.activeElement?.tagName)){const el=document.activeElement;if(el.tagName==='BUTTON'||el.type==='checkbox')el.click();}
      if(tap(1)&&!modal)show('');
      if(!modal&&!editor.hidden){if(tap(4))ctx.seek(ctx.time()-5);if(tap(5))ctx.seek(ctx.time()+5);if(tap(2))$('#studio-play').click();}
      lastButtons=pressed;
    }
    const enabled=$('#studio-handheld-gamepad');enabled.checked=localStorage.getItem(key+'-gamepad')==='true';
    function controller(){clearInterval(padTimer);padTimer=enabled.checked?setInterval(poll,100):null;lastButtons=[];}
    enabled.onchange=()=>{localStorage.setItem(key+'-gamepad',enabled.checked);controller();};controller();
    addEventListener('resize',update);update();return {update,panel,show};
  };
})();
