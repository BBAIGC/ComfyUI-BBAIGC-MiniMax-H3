import {ComfyWidgets} from '../../scripts/widgets.js';
import {app} from '../../scripts/app.js';

const schemas=new Map();
const API_FIELDS={'服务来源':[['本地模型','BBAPI'],{default:'本地模型'}],'API配置名称':['STRING',{default:'bbapi-main'}],'API接口地址':['STRING',{default:'https://bbapi.ainivox.cn'}],'API模型':['STRING',{default:''}]};
const VIDEO_FIELDS={'视频LoRA':['STRING',{default:'[]'}],'视频精修':['STRING',{default:'{}'}],'显存预留GB':['FLOAT',{default:0,min:0,max:12,step:.5}],'加速LoRA档位':[[4,8],{default:8}]};
export function rememberModelSchema(data){
 data.input||={};data.input.optional||={};
 const extras=data.name==='H3TextModelSettings'?{...API_FIELDS,'API协议':[['自动','Responses','Chat Completions'],{default:'自动'}]}:data.name==='H3ImageModelSettings'?{...API_FIELDS,'API分辨率':[['1K','2K','4K'],{default:'1K'}],'API质量':[['low','medium','high','auto'],{default:'low'}],'图像LoRA':['STRING',{default:'[]'}]}:VIDEO_FIELDS;
 for(const[name,definition]of Object.entries(extras))data.input.optional[name]??=definition;
 if(data.input.required?.['加速步数'])data.input.required['加速步数']=['INT',{default:8,min:1,max:100}];
 schemas.set(data.name,data);
}
export const modelSchema=type=>schemas.get(type);

export function ensureModelWidgets(node,saved=null){
 const schema=schemas.get(node?.type);if(!node||!schema)return [];
 const added=[],definitions={...schema.input?.required,...schema.input?.optional};
 for(const[name,definition]of Object.entries(definitions)){
  const kind=definition[0],options=definition[1]||{};
  if(options.forceInput||!Array.isArray(kind)&&!['STRING','INT','FLOAT','BOOLEAN'].includes(kind))continue;
  let widget=node.widgets?.find(item=>item.name===name);
  if(!widget){const factory=ComfyWidgets[Array.isArray(kind)?'COMBO':kind];if(!factory)continue;widget=factory(node,name,definition,app).widget;if(widget)added.push(widget);}
  if(!widget)continue;
  const savedValue=saved?.widgets_values_named?.[name];
  if(savedValue!==undefined){
   const valid=Array.isArray(kind)?kind.includes(savedValue):kind==='BOOLEAN'?typeof savedValue==='boolean':kind==='INT'?Number.isInteger(savedValue):kind==='FLOAT'?typeof savedValue==='number'&&Number.isFinite(savedValue):kind==='STRING'?typeof savedValue==='string':false;
   if(valid)widget.value=savedValue;
  }
  widget.options||={};widget.options.serialize=true;
  if(name==='加速步数'&&kind==='INT'){widget.type='number';Object.assign(widget.options,{min:1,max:100,step:10,precision:0});delete widget.options.values;const input=node.inputs?.find(input=>input.name===name);if(input)input.type='INT';}
 }
 return added;
}

export function modelChoices(node,name){
 const definition=modelSchema(node?.type)?.input?.required?.[name]||modelSchema(node?.type)?.input?.optional?.[name];
 let options=definition?.[0];if(!Array.isArray(options))options=node?.widgets?.find(item=>item.name===name)?.options?.values;
 if(typeof options==='function')options=options();return Array.isArray(options)?options:[];
}
