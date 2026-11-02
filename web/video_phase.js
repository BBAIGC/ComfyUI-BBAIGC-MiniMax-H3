const labels={loading:'加载视频模型',encoding:'编码视频条件',sampling:'视频采样',lora_refining:'LoRA 细化',latent_refining:'二次采样',video_decoding:'解码视频画面',face_detection:'检测人脸',face_encoding:'编码脸修条件',face_sampling:'脸部修复采样',face_decoding:'解码脸修画面',face_stitching:'融合修复人脸',audio_decoding:'解码音频',video_encoding:'保存镜头视频'};
const sampled=new Set(['sampling','lora_refining','latent_refining','face_sampling']);
export function videoPhaseView(scene,snapshot,index,now=Date.now()/1000){
 const busy=!!scene&&!scene.video&&['running','generating'].includes(snapshot?.status)&&snapshot?.scene_index===index+1&&['生成中','running','generating'].includes(scene.status);
 if(!busy)return {busy:false,text:''};
 const state=scene.video_progress||snapshot.video_progress||{},phase=state.phase,label=labels[phase]||'生成视频';
 const start=Number(state.started_at),elapsed=Number.isFinite(start)&&start>0?` · ${Math.max(0,Math.floor(now-start))}秒`:'';
 const value=Number(state.value),total=Number(state.total),fraction=sampled.has(phase)&&Number.isFinite(value)&&Number.isFinite(total)&&total>0&&value>=0&&value<=total?` · ${value}/${total} · ${Math.round(value/total*100)}%`:'';
 return {busy:true,text:`${label}${state.slot?` ${state.slot}`:''}${fraction}${elapsed}`};
}
