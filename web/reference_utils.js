const REFERENCE_TOKEN = /@(图[1-9]\d*)/g;

/** Serialize image chips as stable text tokens, including their pending state. */
export function editorPlainText(container) {
    function textOf(node) {
        if (node.nodeType === 3) return node.nodeValue || "";
        if (node.nodeType !== 1 && node.nodeType !== 11) return "";
        if (node.nodeType === 1) {
            if (node.dataset?.qwsReferenceText) return node.dataset.qwsReferenceText;
            if (node.dataset?.h3Reference) return `@${node.dataset.h3Reference}`;
            if (node.tagName === "BR") return "\n";
            if (node.getAttribute("aria-hidden") === "true") return "";
        }
        let text = "";
        for (const child of node.childNodes) {
            const block = child.nodeType === 1 && ["DIV","P","LI"].includes(child.tagName);
            if (block && text && !text.endsWith("\n")) text += "\n";
            text += textOf(child);
            if (block && child.nextSibling && !text.endsWith("\n")) text += "\n";
        }
        return text;
    }
    return textOf(container).replace(/\u200b/g, "").replace(/\u00a0/g, " ");
}

/** Stable identity is distinct from the current, contiguous display number. */
export function referenceIdentity(entry) {
    if(entry?.uid)return entry.uid;
    if(!entry?.filename)return `external-${entry?.id||''}`;
    const source=[entry.type||'input',entry.subfolder||'',entry.filename,entry.id||''].join('/');let hash=14695981039346656037n;
    for(const char of source){hash^=BigInt(char.codePointAt(0));hash=BigInt.asUintN(64,hash*1099511628211n);}
    return `legacy-${hash.toString(16)}`;
}

export function numberedReferences(list) {
    return (Array.isArray(list)?list:[]).map((entry,index)=>({...entry,uid:referenceIdentity(entry),id:`ref${index+1}`}));
}

export const QWS_REFERENCE = /@未引用\[([^\]\s]+)\]|@?(?:参考图|素材图|图片|图像|图|ref)\s*([1-9]\d*|[一二两三四五六七八九十]+)(?![\d一二两三四五六七八九十])|<image([1-9]\d*)>|\bimage\s*([1-9]\d*)\b/gi;
export function referenceTokens(text) {
    return [...String(text??'').matchAll(QWS_REFERENCE)].map(match=>({start:match.index,end:match.index+match[0].length,token:match[0],missing:match[1]||null,number:referenceNumber(match[2]||match[3]||match[4]||'0')||0}));
}

/** Rebase preserved occurrences through one edit, then resolve by uid in one pass. */
export function bindReferenceText(value, references, previous=null) {
    const raw=String(value??''),old=String(previous?.text??''),bindings=previous?.bindings||[];
    let prefix=0,suffix=0;while(prefix<raw.length&&prefix<old.length&&raw[prefix]===old[prefix])prefix++;
    while(suffix<raw.length-prefix&&suffix<old.length-prefix&&raw[raw.length-1-suffix]===old[old.length-1-suffix])suffix++;
    const shifted=new Map();
    for(const binding of bindings){let start=binding.start,end=binding.end??start+String(binding.token||'').length;if(raw===old||end<=prefix){}else if(start>=old.length-suffix){start+=raw.length-old.length;end+=raw.length-old.length;}else continue;shifted.set(start,{...binding,start,end});}
    const byNumber=new Map(references.map(item=>[Number(String(item.id).replace(/^ref/,'')),item])),byUid=new Map(references.map(item=>[referenceIdentity(item),item]));
    let text='',position=0;const next=[];
    for(const token of referenceTokens(raw)){
        text+=raw.slice(position,token.start);
        const prior=shifted.get(token.start),uid=token.missing||(prior&&prior.end===token.end?prior.uid:null)||referenceIdentity(byNumber.get(token.number)||{uid:`missing-number-${token.number}`});
        const reference=byUid.get(uid),number=reference?Number(String(reference.id).replace(/^ref/,'')):token.number;
        const replacement=reference?`@参考图${number}`:`@未引用[${uid}]`,start=text.length;
        text+=replacement;next.push({start,end:text.length,uid,number,token:replacement});position=token.end;
    }
    text+=raw.slice(position);return {version:1,text,bindings:next};
}

export function mapModelReferences(text, snapshot) {
    const bySlot=new Map((snapshot||[]).map(item=>[Number(item.slot),item]));
    return String(text??'').replace(/<image([1-9]\d*)>|\bimage\s*([1-9]\d*)\b|@?(?:参考图|图)\s*([1-9]\d*)(?!\d)/gi,(token,a,b,c)=>{
        const item=bySlot.get(Number(a||b||c));return item?`@参考图${String(item.id).replace(/^ref/,'')}`:token;
    });
}

/** A partial DOM selection inside a chip still represents that whole reference. */
export function editorClipboardSelection(editor, selection) {
    if (!selection?.rangeCount || selection.isCollapsed) return null;
    const range = selection.getRangeAt(0).cloneRange();
    if (!range.intersectsNode(editor)) return null;
    if (!editor.contains(range.startContainer)) range.setStart(editor, 0);
    if (!editor.contains(range.endContainer)) range.setEnd(editor, editor.childNodes.length);
    const chipAt = node => {
        const element = node.nodeType === 1 ? node : node.parentElement;
        const chip = element?.closest?.("[data-h3-reference],[data-qws-reference-text]");
        return chip && editor.contains(chip) ? chip : null;
    };
    const first = chipAt(range.startContainer), last = chipAt(range.endContainer);
    if (first) range.setStartBefore(first);
    if (last) range.setEndAfter(last);
    return {range, text: editorPlainText(range.cloneContents())};
}

/** References are displayed by their current library order. Rename in one pass. */
export function reconcileReferences(entries, script, {removedIds = []} = {}) {
    const source = Array.isArray(entries) ? entries : [];
    const renamed = new Map();
    const removed = new Set(removedIds.map(String));
    const references = source.map((entry, index) => {
        const id = `图${index + 1}`;
        if (entry?.id) renamed.set(String(entry.id), id);
        return {...entry, id};
    });
    const available = new Set(references.map(entry => entry.id));
    const original = String(script ?? "");
    const text = original.replace(REFERENCE_TOKEN, (token, id) => {
        if (!references.length) return "";
        if (renamed.has(id)) return `@${renamed.get(id)}`;
        // A removed slot now belongs to the following image, as displayed in
        // the renumbered library. A removed final slot has no replacement.
        if (removed.has(id)) return available.has(id) ? `@${id}` : "";
        // Repair older projects whose library started at 图2 but script used 图1.
        return token;
    });
    const changed = text !== original || references.some((entry, index) => entry.id !== source[index]?.id);
    return {references, script: text, changed};
}


const PROTECTED_SPEECH = /<d(?:\s[^>]*)?>[\s\S]*?<\/d>|“[^”]*”|「[^」]*」|『[^』]*』|"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'/gi;
const NUMBER_WORDS = {zero:0,one:1,two:2,three:3,four:4,five:5,six:6,seven:7,eight:8,nine:9,ten:10,eleven:11,twelve:12,thirteen:13,fourteen:14,fifteen:15,sixteen:16,seventeen:17,eighteen:18,nineteen:19,twenty:20,thirty:30,forty:40,fifty:50,sixty:60,seventy:70,eighty:80,ninety:90};
const NUMBER_WORD_PATTERN = "(?:"+Object.keys(NUMBER_WORDS).join("|")+"|hundred)";
const ENGLISH_REFERENCE = new RegExp("\\b(?:picture|image|photo|reference(?: image)?|number)\\s*(?:no\\.?|#)?\\s*(\\d+|"+NUMBER_WORD_PATTERN+"(?:[ -]+(?:and[ -]+)?"+NUMBER_WORD_PATTERN+"){0,4})\\b","gi");
function referenceNumber(raw){
    if(/^\d+$/.test(raw))return Number(raw);
    if(/^[零〇一二两三四五六七八九十百]+$/.test(raw)){const digits={零:0,〇:0,一:1,二:2,两:2,三:3,四:4,五:5,六:6,七:7,八:8,九:9};let total=0,last=0;for(const char of raw){if(char==="十"||char==="百"){total+=(last||1)*(char==="十"?10:100);last=0;}else last=digits[char];}return total+last;}
    let total=0;for(const word of raw.toLowerCase().split(/[ -]+/)){if(word==="and")continue;if(word==="hundred")total=(total||1)*100;else if(word in NUMBER_WORDS)total+=NUMBER_WORDS[word];else return NaN;}return total;
}
/** Normalize only explicit material references. Quoted and <d> speech stays verbatim. */
export function normalizeReferenceMentions(script,count){
    const original=String(script??""),available=Math.max(0,Math.min(101,Number(count)||0));
    const protectedSegments=[];const skeleton=original.replace(PROTECTED_SPEECH,part=>{protectedSegments.push(part);return `\u0000H3S${protectedSegments.length-1}\u0000`;});
    const canonical=(token,raw)=>{const number=referenceNumber(raw);return number>=1&&number<=available?`@图${number}`:token;};
    let normalized=skeleton.replace(/(?<!@)(?:参考图|素材图|图片|图像|图)\s*([1-9]\d*|[零〇一二两三四五六七八九十百]+)(?![零〇一二两三四五六七八九十百\d])/g,canonical).replace(ENGLISH_REFERENCE,canonical);
    const ids=Array.from(new Set(Array.from(normalized.matchAll(/@图([1-9]\d*)/g),match=>Number(match[1])).filter(number=>number<=available)));
    if(available===1&&!ids.length){normalized=`@图1\n${normalized}`;ids.push(1);}
    normalized=normalized.replace(/\u0000H3S(\d+)\u0000/g,(_,index)=>protectedSegments[Number(index)]);
    const valid=available<=1||ids.length>0;
    return {script:normalized,changed:normalized!==original,references:ids.map(number=>`图${number}`),valid,error:valid?"":"有多张参考图，请在剧本中用 @ 指定图片。"};
}
