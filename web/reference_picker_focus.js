// A missing-reference chip is focusable inside the contenteditable editor.
// Moving focus to that chip must not let the previous editor blur close a
// newly opened picker. Treat the editor and picker as one focus region.
export function bindReferencePickerFocus(editor,picker,onClose,signal){
    const document=editor.ownerDocument;
    let pending=null;
    const cancel=()=>{if(pending!==null){clearTimeout(pending);pending=null;}};
    const leaving=()=>{
        cancel();
        pending=setTimeout(()=>{
            pending=null;
            const active=document.activeElement;
            if(!signal?.aborted&&!editor.contains(active)&&!picker.contains(active))onClose();
        },120);
    };
    for(const element of [editor,picker]){
        element.addEventListener('focusin',cancel,{signal});
        element.addEventListener('focusout',leaving,{signal});
    }
    signal?.addEventListener('abort',cancel,{once:true});
    return {cancel};
}
