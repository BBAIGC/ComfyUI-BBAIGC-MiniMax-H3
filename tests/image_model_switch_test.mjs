import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createImageSamplingControls} from '../web/image_sampling_controls.js';

// The controls only need native element properties for this explicit-change
// integration check; no browser, Comfy server or model is started.
class Element{
 constructor(tag){this.tagName=tag.toUpperCase();this.children=[];this.attributes={};this.dataset={};this.style={};this.value='';}
 append(...nodes){this.children.push(...nodes);}
 replaceChildren(...nodes){this.children=[...nodes];}
 setAttribute(name,value){this.attributes[name]=String(value);}
 querySelectorAll(){return [];}
 focus(){document.activeElement=this;}
}
globalThis.document={head:new Element('head'),activeElement:null,createElement:tag=>new Element(tag),getElementById(){return null;}};
globalThis.Option=class extends Element{constructor(label,value){super('option');this.textContent=label;this.value=value;}};
const fields={model:'图像模型',steps:'步数',cfg:'CFG',denoise:'去噪',sampler:'采样器',scheduler:'调度器'};
const normal='qwen-image-2.1-UC-Q6_K.gguf',turbo='qwen-image-2.1-Turbo-Q6_K.gguf',turboAlt='qwen-image-2.1-Turbo-Q8_0.gguf';
const node={properties:{},graph:{setDirtyCanvas(){}},widgets:Object.entries(fields).map(([key,name])=>({name,value:{model:normal,steps:25,cfg:1,denoise:1,sampler:'euler',scheduler:'simple'}[key],options:{values:key==='model'?[normal,turbo,turboAlt]:key==='sampler'?['euler','dpmpp_2m']:key==='scheduler'?['simple','normal']:undefined}}))};
const widget=name=>node.widgets.find(w=>w.name===name);
const control=(root,label)=>{if(root.attributes['aria-label']===label)return root;for(const child of root.children){const found=control(child,label);if(found)return found;}return null;};
let changed=0;
const sampling=createImageSamplingControls({node,fields,onChange(){changed++;}});
// User tuning belongs independently to each real model version.
widget('步数').value=30;widget('CFG').value=2.5;sampling.refresh();
assert.equal(sampling.selectModel(turbo),true);
assert.equal(widget('图像模型').value,turbo);assert.equal(widget('步数').value,8);assert.equal(widget('CFG').value,1);
widget('步数').value=12;widget('CFG').value=1.5;sampling.refresh();
assert.equal(sampling.selectModel(normal),true);
assert.equal(widget('步数').value,30);assert.equal(widget('CFG').value,2.5);
sampling.selectModel(turbo);assert.equal(widget('步数').value,12);assert.equal(widget('CFG').value,1.5);
sampling.selectModel(turboAlt);assert.equal(widget('图像模型').value,turboAlt);assert.equal(widget('步数').value,12);
const before=changed;assert.equal(sampling.selectModel('missing-model.gguf'),false);assert.equal(changed,before);

// A saved/programmatic widget restore stays authoritative even when unusual.
widget('图像模型').value=turbo;widget('步数').value=25;sampling.refresh();
assert.equal(widget('步数').value,25);

// Exercise the top model/API panel's real onchange handler against that same
// sampler owner. Replace only the external Comfy API import for this test.
const source=await readFile(new URL('../web/model_services.js',import.meta.url),'utf8');
const {createModelServices}=await import('data:text/javascript;base64,'+Buffer.from(source.replace("import {api} from '../../scripts/api.js';","const api={fetchApi(){throw Error('No network expected');}};")).toString('base64'));
const language={widgets:[{name:'模型',value:'text-old'}]};
let localCalls=0,panelChanges=0;
const panel=createModelServices({source:kind=>kind==='image'?node:language,isBusy:()=>false,onChange(){panelChanges++;},choices:kind=>kind==='image'?[normal,turbo,turboAlt]:['text-old','text-new'],onLocalModelChange(kind,name){localCalls++;return kind==='image'?sampling.selectModel(name):false;}});
panel.refresh();assert.equal(localCalls,0);assert.equal(widget('步数').value,25);
const image=control(panel.element,'图片本地模型');
image.value=normal;image.onchange();assert.equal(widget('图像模型').value,normal);assert.equal(widget('步数').value,30);
image.value=turbo;image.onchange();assert.equal(widget('图像模型').value,turbo);assert.equal(widget('步数').value,25);
const languageControl=control(panel.element,'语言本地模型');languageControl.value='text-new';languageControl.onchange();
assert.equal(language.widgets[0].value,'text-new');assert.equal(widget('步数').value,25);assert.equal(panelChanges,1);
node.widgets.push({name:'API模型',value:''});
const apiModel=control(panel.element,'图片API模型');apiModel.value='cloud-image';apiModel.onchange();
assert.equal(widget('API模型').value,'cloud-image');assert.equal(widget('步数').value,25);assert.equal(localCalls,3);
sampling.dispose();assert.equal(sampling.selectModel(normal),false);
console.log('PASS: explicit top-panel model switches use real normal/Turbo defaults and independent user settings; restore/text/API remain authoritative');
