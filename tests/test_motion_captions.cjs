const assert=require('node:assert/strict');
const M=require('../clipmaker/static/motion-captions.js');
const motion={...M.defaults(),keyframes:[{time:10,x:30,y:20},{time:12,x:80,y:40}]};
assert.deepEqual(M.position(motion,11),{x:55,y:30});
assert.equal(M.pose({...motion,effect:'grow'},11,10,12).size,1.25);
assert.deepEqual(M.geometry(1920,1080,{mode:'fit'}),{w:1080,h:607.5,x:0,y:656.25});
const filled=M.geometry(1920,1080,{mode:'fill'});
assert.equal(filled.w,1920*(1920/1080));assert.equal(filled.x,-(filled.w-1080)/2);assert.ok(Math.abs(filled.y)<.001);
// Track a textured target after a real pixel translation; reject a lost target.
const width=100,height=100,box={x:30,y:25,w:20,h:20};
function frame(dx,dy){const a=new Float32Array(width*height);for(let y=0;y<20;y++)for(let x=0;x<20;x++)a[(box.y+y+dy)*width+box.x+x+dx]=(x*37+y*61+x*y*13)%255;return a;}
const model=M.template(frame(0,0),width,box);
const best=M.match(frame(8,6),width,height,model,box,16);
assert.equal(best.x,38);assert.equal(best.y,31);assert.ok(best.score>.99);
assert.ok(M.match(new Float32Array(width*height),width,height,model,best).score<.68);
assert.throws(()=>M.template(new Float32Array(width*height),width,box),/detailed area/);
console.log('Motion path, framing, tracker translation and target-loss checks passed.');
