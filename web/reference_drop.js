const IMAGE_MIMES=['application/x-mjr-assets','application/x-mjr-asset','application/x-comfy-asset-info','application/x-h3-images','application/x-h3-image'];
const IMAGE_EXT=/\.(png|jpe?g|webp|bmp|gif|avif)$/i;
export function hasImagePayload(transfer){return Array.from(transfer?.types||[]).some(type=>IMAGE_MIMES.includes(type));}
export function canDropImages(transfer){return hasImagePayload(transfer)||Array.from(transfer?.types||[]).includes('Files');}
export function isImageFile(file){return file?.type?.startsWith('image/')||IMAGE_EXT.test(file?.name||'');}

export async function readDroppedImages(transfer,{fetchApi,signal,limit=101}={}){
    // Read the entire drag payload before awaiting: browsers protect it again
    // as soon as the synchronous drop handler returns.
    let entries=null;
    for(const mime of IMAGE_MIMES){
        const raw=transfer?.getData?.(mime);if(!raw)continue;
        try{const parsed=JSON.parse(raw);entries=Array.isArray(parsed)?parsed:parsed.items||parsed.images||[parsed];break;}
        catch{throw Error('拖入的图片信息无法读取。');}
    }
    if(!entries)return Array.from(transfer?.files||[]).filter(isImageFile).slice(0,limit);
    if(!Array.isArray(entries))throw Error('拖入的图片信息无效。');
    const images=entries.filter(item=>item&&typeof item.filename==='string'&&IMAGE_EXT.test(item.filename)&&(!item.kind||item.kind==='image')).slice(0,limit);
    if(!images.length)throw Error('请拖入图片素材。');
    const result=[];
    for(const item of images){
        const image={filename:item.filename,subfolder:item.subfolder||'',type:item.type||'output'};
        if(item.root_id){
            const response=await fetchApi('/mjr/am/stage-to-input',{method:'POST',headers:{'Content-Type':'application/json'},signal,
                body:JSON.stringify({index:false,files:[{...image,root_id:item.root_id,dest_subfolder:''}]})});
            const data=await response.json();
            const staged=(data.data?.staged||data.staged)?.[0];
            if(!response.ok||data.ok===false||!staged?.name)throw Error(data.error||'资源图片无法导入，请检查素材是否仍存在。');
            result.push({filename:staged.name,subfolder:staged.subfolder||'',type:'input'});
        }else{
            if(!['input','output','temp'].includes(image.type))throw Error('图片来源无效。');
            result.push(image);
        }
    }
    return result;
}

export function installDropCleanup(root,clear,{signal,onEnd=()=>{},timeout=1200}={}){
    const doc=root.ownerDocument,view=doc.defaultView;
    let timer=null;
    const reset=()=>{if(timer!==null)view.clearTimeout(timer);timer=null;clear();};
    const touch=()=>{if(timer!==null)view.clearTimeout(timer);timer=view.setTimeout(reset,timeout);};
    const end=()=>{reset();onEnd();};
    const options={capture:true,signal};
    root.addEventListener('dragenter',touch,options);
    root.addEventListener('dragover',touch,options);
    root.addEventListener('drop',reset,options);
    view.addEventListener('drop',reset,options);
    view.addEventListener('dragend',end,options);
    view.addEventListener('blur',end,{signal});
    view.addEventListener('keydown',event=>{if(event.key==='Escape')end();},options);
    view.addEventListener('pointermove',event=>{if(event.buttons===0)reset();},options);
    doc.addEventListener('dragleave',event=>{if(!event.relatedTarget&&(event.target===doc||event.target===doc.documentElement))reset();},options);
    signal?.addEventListener('abort',reset,{once:true});
    return {reset,touch};
}
