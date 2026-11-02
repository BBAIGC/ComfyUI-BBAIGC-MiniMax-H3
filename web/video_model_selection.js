const FUSED_WEIGHTS=new Set(['原作者融合模型','mystic07 · RefDelta r1024 Turbo8']);
const SUPPORTED_WEIGHTS=new Set([...FUSED_WEIGHTS,'独立LoRA','Pika H3 Flex · 6步']);

export function restoreLegacyTextVideoWeights(node){
 const backups=node.properties?.h3_text_video_weights;
 if(!backups||typeof backups!=='object'||Array.isArray(backups))return false;
 let changed=false;
 for(const[id,previous]of Object.entries(backups)){
  const source=id==='self'?node:node.graph?.getNodeById?.(Number(id))??node.graph?.getNodeById?.(id);
  if(!source)continue;
  const widget=source.widgets?.find(item=>item.name==='加速权重');
  if(widget?.value==='独立LoRA'&&SUPPORTED_WEIGHTS.has(previous)&&previous!==widget.value){
   widget.value=previous;if(widget.inputEl)widget.inputEl.value=previous;source.graph?.setDirtyCanvas?.(true,true);changed=true;
  }
  // A different current route is an explicit newer choice. Preserve it and
  // retire the old automatic-switch receipt exactly once.
  delete backups[id];
 }
 if(!Object.keys(backups).length)delete node.properties.h3_text_video_weights;
 return changed;
}

export function videoModelSummary(settings={}){
 const accelerated=!!settings['启用加速LoRA'],weight=settings['加速权重'];
 const model=accelerated&&FUSED_WEIGHTS.has(weight)?settings['原作者模型']:settings['基础模型'];
 const route=!accelerated?'基础模型':FUSED_WEIGHTS.has(weight)?'融合模型':weight==='Pika H3 Flex · 6步'?'Pika 6步':`独立LoRA ${settings['加速步数']||8}步`;
 return {model,route};
}
