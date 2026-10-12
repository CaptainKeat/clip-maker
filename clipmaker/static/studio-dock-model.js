(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.StudioDockModel=api;})(typeof window==='undefined'?globalThis:window,()=>{
  'use strict';
  const panels=['sources','source','program','controls','effects','bookmarks','timeline'],names={sources:'Sources',source:'Source preview',program:'Program',controls:'Effect Controls',effects:'Effects',bookmarks:'Bookmarks',timeline:'Timeline'};
  const clone=v=>JSON.parse(JSON.stringify(v)),uid=()=>globalThis.crypto.randomUUID(),clamp=(n,a,b)=>Math.max(a,Math.min(b,n));
  const number=(v,fallback)=>Number.isFinite(Number(v))?Number(v):fallback;
  const tabs=(ids,active=ids[0])=>({kind:'tabs',id:uid(),panels:ids,active}),split=(axis,a,b,ratio=.5)=>({kind:'split',id:uid(),axis,ratio,a,b});
  function preset(name='editing'){
    const project=tabs(['sources','source','bookmarks']),program=tabs(['program']),controls=tabs(['controls']),effects=tabs(['effects']),timeline=tabs(['timeline']);
    const editing=split('y',split('x',project,split('x',program,tabs(['controls','effects'],'controls'),.72),.23),timeline,.64);
    const other=split('y',split('x',controls,split('x',program,effects,.7),.25),split('x',project,timeline,.25),.64);
    return {tree:name==='editing'?editing:other,floats:[],closed:[],maximized:null};
  }
  function normalize(raw){
    const seen=new Set(),ids=new Set();let count=0;
    function node(n,depth=0){if(!n||typeof n!=='object'||depth>12||++count>40)return null;let id=typeof n.id==='string'&&n.id.length<=80&&!ids.has(n.id)?n.id:uid();ids.add(id);
      if(n.kind==='split'){const a=node(n.a,depth+1),b=node(n.b,depth+1);if(!a)return b;if(!b)return a;return {kind:'split',id,axis:n.axis==='y'?'y':'x',ratio:clamp(Number(n.ratio)||.5,.08,.92),a,b};}
      if(n.kind!=='tabs')return null;const values=(Array.isArray(n.panels)?n.panels:[]).filter(p=>panels.includes(p)&&!seen.has(p)&&seen.add(p));if(!values.length)return null;return {kind:'tabs',id,panels:values,active:values.includes(n.active)?n.active:values[0]};
    }
    const tree=node(raw?.tree),floats=[];
    for(const f of (Array.isArray(raw?.floats)?raw.floats:[]).slice(0,7)){const n=node(f.node);if(n)floats.push({id:typeof f.id==='string'&&f.id.length<=80&&!floats.some(other=>other.id===f.id)?f.id:uid(),node:n,x:clamp(number(f.x,40),0,4000),y:clamp(number(f.y,30),0,2400),w:clamp(number(f.w,360),180,2400),h:clamp(number(f.h,300),140,1800)});}
    if(!tree&&!floats.length)return preset();return {tree,floats,closed:panels.filter(p=>!seen.has(p)),maximized:null};
  }
  function walk(n,fn){if(!n)return;fn(n);if(n.kind==='split'){walk(n.a,fn);walk(n.b,fn);}}
  function find(layout,id){let found=null;walk(layout.tree,n=>{if(n.id===id)found=n;});for(const f of layout.floats)walk(f.node,n=>{if(n.id===id)found=n;});return found;}
  function owner(layout,panel){let found=null;walk(layout.tree,n=>{if(n.panels?.includes(panel))found=n;});for(const f of layout.floats)walk(f.node,n=>{if(n.panels?.includes(panel))found=n;});return found;}
  function removeNode(n,panel){if(!n)return null;if(n.kind==='tabs'){n.panels=n.panels.filter(p=>p!==panel);if(!n.panels.length)return null;if(!n.panels.includes(n.active))n.active=n.panels[0];return n;}n.a=removeNode(n.a,panel);n.b=removeNode(n.b,panel);return !n.a?n.b:!n.b?n.a:n;}
  function remove(layout,panel){const out=clone(layout);out.tree=removeNode(out.tree,panel);out.floats=out.floats.map(f=>({...f,node:removeNode(f.node,panel)})).filter(f=>f.node);out.closed=panels.filter(p=>!owner(out,p));out.maximized=null;return out;}
  function replace(n,id,value){if(!n)return n;if(n.id===id)return value;if(n.kind==='split'){n.a=replace(n.a,id,value);n.b=replace(n.b,id,value);}return n;}
  function dock(layout,panel,targetId,zone='center'){
    if(!panels.includes(panel)||!['center','left','right','top','bottom'].includes(zone))throw new Error('Choose a valid panel and docking zone.');
    const original=find(layout,targetId),same=owner(layout,panel);if(original&&same&&original.id===same.id&&same.panels.length===1&&targetId!=='root')return clone(layout);
    const out=remove(layout,panel),target=targetId==='root'?out.tree:find(out,targetId);if(targetId!=='root'&&!target)throw new Error('That docking target is no longer available.');
    if(zone==='center'&&target?.kind==='tabs'){target.panels.push(panel);target.active=panel;}
    else{const fresh=tabs([panel]),axis=['top','bottom'].includes(zone)?'y':'x',before=['left','top'].includes(zone),value=target?split(axis,before?fresh:target,before?target:fresh,before?.3:.7):fresh;
      if(targetId==='root')out.tree=value;else{out.tree=replace(out.tree,targetId,value);for(const f of out.floats)f.node=replace(f.node,targetId,value);}}
    out.closed=panels.filter(p=>!owner(out,p));return out;
  }
  function float(layout,panel,rect={}){const out=remove(layout,panel);out.floats.push({id:uid(),node:tabs([panel]),x:Math.max(0,Number(rect.x)||30),y:Math.max(0,Number(rect.y)||30),w:Math.max(180,Number(rect.w)||360),h:Math.max(140,Number(rect.h)||300)});out.closed=panels.filter(p=>!owner(out,p));return out;}
  function close(layout,panel){if(!panels.includes(panel))throw new Error('Unknown panel.');const out=remove(layout,panel);if(!out.tree&&!out.floats.length)throw new Error('Keep at least one panel open. Reopen panels from the Panels menu.');return out;}
  function activate(layout,panel){const out=clone(layout),node=owner(out,panel);if(node)node.active=panel;else return dock(out,panel,'root','right');out.maximized=null;return out;}
  function resize(layout,id,ratio){const out=clone(layout),n=find(out,id);if(n?.kind==='split')n.ratio=clamp(Number(ratio)||.5,.08,.92);return out;}
  return {panels,names,clone,tabs,split,preset,normalize,find,owner,walk,dock,float,close,activate,resize};
});
