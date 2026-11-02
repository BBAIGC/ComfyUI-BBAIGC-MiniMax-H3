import assert from 'node:assert/strict';
import {test} from 'node:test';
import {bindReferencePickerFocus} from '../web/reference_picker_focus.js';

class Element extends EventTarget{
    constructor(document,parent=null){super();this.ownerDocument=document;this.parent=parent;}
    contains(target){for(let current=target;current;current=current.parent)if(current===this)return true;return false;}
}
function fixture(t){
    t.mock.timers.enable({apis:['setTimeout']});
    const document={activeElement:null},editor=new Element(document),picker=new Element(document);
    const missing=new Element(document,editor),choice=new Element(document,picker),outside=new Element(document);
    const life=new AbortController();let visible=true,closed=0;
    const focus=bindReferencePickerFocus(editor,picker,()=>{visible=false;closed++;},life.signal);
    t.after(()=>life.abort());
    return {document,editor,picker,missing,choice,outside,life,focus,
        get visible(){return visible;},get closed(){return closed;},open(){visible=true;focus.cancel();}};
}

test('a second missing chip keeps its picker after editor-to-chip focus movement',t=>{
    const f=fixture(t);
    f.document.activeElement=f.editor;
    f.editor.dispatchEvent(new Event('focusout'));
    f.document.activeElement=f.missing;
    f.editor.dispatchEvent(new Event('focusin'));
    // repairReferenceChip focuses the editor and opens the new picker.
    f.document.activeElement=f.editor;f.open();
    t.mock.timers.tick(200);
    assert.equal(f.visible,true);assert.equal(f.closed,0);
});

test('a late old focusout cannot close a picker while the editor owns focus',t=>{
    const f=fixture(t);
    f.open();f.editor.dispatchEvent(new Event('focusout'));
    f.document.activeElement=f.editor;
    t.mock.timers.tick(200);
    assert.equal(f.visible,true);
});

test('focus in a picker choice keeps the choices available',t=>{
    const f=fixture(t);
    f.editor.dispatchEvent(new Event('focusout'));
    f.document.activeElement=f.choice;f.picker.dispatchEvent(new Event('focusin'));
    t.mock.timers.tick(200);
    assert.equal(f.visible,true);
});

test('leaving both editor and picker closes it once',t=>{
    const f=fixture(t);
    f.document.activeElement=f.outside;
    f.editor.dispatchEvent(new Event('focusout'));
    f.picker.dispatchEvent(new Event('focusout'));
    t.mock.timers.tick(119);assert.equal(f.visible,true);
    t.mock.timers.tick(1);assert.equal(f.visible,false);assert.equal(f.closed,1);
});

test('destroying the studio cancels pending closure and removes focus handlers',t=>{
    const f=fixture(t);f.document.activeElement=f.outside;
    f.editor.dispatchEvent(new Event('focusout'));f.life.abort();
    t.mock.timers.tick(200);assert.equal(f.closed,0);
    f.editor.dispatchEvent(new Event('focusout'));
    t.mock.timers.tick(200);assert.equal(f.closed,0);
});
