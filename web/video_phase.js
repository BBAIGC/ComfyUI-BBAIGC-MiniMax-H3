const labels={loading:'加载视频模型',encoding:'编码视频条件',sampling:'视频采样',lora_refining:'LoRA 细化',latent_refining:'二次采样',video_decoding:'解码视频画面',face_detection:'检测人脸',face_encoding:'编码脸修条件',face_sampling:'脸部修复采样',face_decoding:'解码脸修画面',face_stitching:'融合修复人脸',audio_decoding:'解码音频',audio_review:'复核对白',video_encoding:'保存镜头视频'};
const sampled=new Set(['sampling','lora_refining','latent_refining','face_sampling']);
export function videoPhaseView(scene,snapshot,index,now=Date.now()/1000){
 const busy=!!scene&&!scene.video&&['running','generating'].includes(snapshot?.status)&&snapshot?.scene_index===index+1&&['生成中','running','generating'].includes(scene.status);
 if(!busy)return {busy:false,text:''};
 const state=scene.video_progress||snapshot.video_progress||{},phase=state.phase,label=labels[phase]||'生成视频';
 const start=Number(state.started_at),elapsed=Number.isFinite(start)&&start>0?` · ${Math.max(0,Math.floor(now-start))}秒`:'';
 const value=Number(state.value),total=Number(state.total),fraction=sampled.has(phase)&&Number.isFinite(value)&&Number.isFinite(total)&&total>0&&value>=0&&value<=total?` · ${value}/${total} · ${Math.round(value/total*100)}%`:'';
 return {busy:true,text:`${label}${state.slot?` ${state.slot}`:''}${fraction}${elapsed}`};
}

export function speechReviewView(review){
 if(!review||!['needs_review','unavailable'].includes(review.status))return {text:'',title:''};
 if(review.status==='unavailable')return {text:'对白未复核',title:review.error||'本机语音识别不可用，请播放检查声音。'};
 return {text:'对白待复核',title:`原句：${(review.expected||[]).join(' ')}\n识别：${review.recognized||'未识别到对白'}\n语音识别可能有误，请播放检查。`};
}

export function speechReviewSummaryText(review){
 if(!review)return '';
 const pending=review.needs_review||[],unavailable=review.unavailable||[];
 return [pending.length?`镜头${pending.join('、')}对白待复核`:'',unavailable.length?`镜头${unavailable.join('、')}对白未复核`:''].filter(Boolean).join('；');
}
