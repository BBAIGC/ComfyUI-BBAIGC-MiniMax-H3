import assert from 'node:assert/strict';
import {test} from 'node:test';
import {canDropImages,hasImagePayload,readDroppedImages,installDropCleanup} from '../web/reference_drop.js';
function transfer(data,files=[]){return {types:Object.keys(data),files,getData:type=>data[type]||''};}

test('resource metadata without Files and ordered multi-selection are accepted once',async()=>{
 const first={filename:'一.png',type:'output',subfolder:'作品',kind:'image'};
 const second={filename:'二.png',type:'input',subfolder:'refs',kind:'image'};
 const dt=transfer({'application/x-mjr-asset':JSON.stringify(first),'application/x-mjr-assets':JSON.stringify({items:[first,second]})});
 assert.equal(canDropImages(dt),true);assert.equal(hasImagePayload(dt),true);
 assert.deepEqual(await readDroppedImages(dt),[{filename:'一.png',subfolder:'作品',type:'output'},{filename:'二.png',subfolder:'refs',type:'input'}]);
});
test('native Comfy asset and OS file drops keep their separate source',async()=>{
 assert.deepEqual(await readDroppedImages(transfer({'application/x-comfy-asset-info':JSON.stringify({filename:'a.webp',type:'input'})})),[{filename:'a.webp',subfolder:'',type:'input'}]);
 const image={name:'file.png',type:'image/png'},text={name:'story.txt',type:'text/plain'};
 assert.deepEqual(await readDroppedImages(transfer({Files:''},[image,text])),[image]);
});
test('custom root images use the existing staging API and capture data before awaiting',async()=>{
 const dt=transfer({'application/x-mjr-assets':JSON.stringify({items:[{filename:'a.png',root_id:'custom-root',type:'custom'},{filename:'b.png',type:'input'}]})});
 const calls=[];
 const images=await readDroppedImages(dt,{fetchApi:async(path,options)=>{
   dt.getData=()=>{throw Error('protected drag payload');};calls.push([path,JSON.parse(options.body)]);
   return {ok:true,json:async()=>({ok:true,data:{staged:[{name:'staged.png',subfolder:'assets'}]}})};
 }});
 assert.equal(calls[0][0],'/mjr/am/stage-to-input');assert.equal(calls[0][1].index,false);
 assert.equal(calls[0][1].files[0].root_id,'custom-root');
 assert.deepEqual(images,[{filename:'staged.png',subfolder:'assets',type:'input'},{filename:'b.png',subfolder:'',type:'input'}]);
});
test('invalid and failed metadata imports report failure instead of falling through to canvas',async()=>{
 await assert.rejects(readDroppedImages(transfer({'application/x-mjr-asset':'bad-json'})),/无法读取/);
 await assert.rejects(readDroppedImages(transfer({'application/x-mjr-asset':JSON.stringify({filename:'movie.mp4',kind:'video'})})),/图片素材/);
 await assert.rejects(readDroppedImages(transfer({'application/x-mjr-asset':JSON.stringify({filename:'a.png',root_id:'gone'})}),{fetchApi:async()=>({ok:false,json:async()=>({ok:false,error:'素材不存在'})})}),/素材不存在/);
});
function scene(){
 const view=new EventTarget(),doc=new EventTarget(),root=new EventTarget(),jobs=new Map();let serial=0;
 view.setTimeout=fn=>{const id=++serial;jobs.set(id,fn);return id;};view.clearTimeout=id=>jobs.delete(id);
 doc.defaultView=view;root.ownerDocument=doc;root.dataset={};
 const controller=new AbortController();let ends=0;
 installDropCleanup(root,()=>{delete root.dataset.fileDrop;},{signal:controller.signal,onEnd:()=>ends++});
 const mark=()=>{root.dataset.fileDrop='true';root.dispatchEvent(new Event('dragover'));};
 return {view,doc,root,jobs,controller,mark,get ends(){return ends;}};
}
test('drop, dragend, Escape, blur and an idle pointer all clear drag feedback',()=>{
 const s=scene();for(const type of ['drop','dragend','blur','keydown','pointermove']){
  s.mark();const event=new Event(type);Object.assign(event,type==='keydown'?{key:'Escape'}:type==='pointermove'?{buttons:0}:{});
  s.view.dispatchEvent(event);assert.equal(s.root.dataset.fileDrop,undefined,type);assert.equal(s.jobs.size,0);
 }assert.equal(s.ends,3);s.controller.abort();
});
test('missing drop/dragend still expires feedback and disposal cancels its watchdog',()=>{
 const s=scene();s.mark();assert.equal(s.jobs.size,1);[...s.jobs.values()][0]();assert.equal(s.root.dataset.fileDrop,undefined);
 s.mark();s.controller.abort();assert.equal(s.jobs.size,0);s.root.dataset.fileDrop='untouched';s.view.dispatchEvent(new Event('dragend'));assert.equal(s.root.dataset.fileDrop,'untouched');
});
