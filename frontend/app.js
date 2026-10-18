const $ = (id) => document.getElementById(id);
const state = { lesson: null, point: null, currentPage: null, manualPage: null, drawMode: false, drag: null, draftRegion: null, draftFocusRegion: null, focusEvent: null, editingFocus: false, activeId: null, editingBoard: null, boardSteps: [], removeImage: false };

const fmt = (seconds) => `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
const api = async (path, options) => {
  const response = await fetch(path, options);
  if (!response.ok) {
    let detail = response.statusText;
    try { detail = (await response.json()).detail || detail; } catch (_) {}
    throw new Error(detail);
  }
  return response.json();
};

function openDialog() { $("uploadDialog").classList.remove("hidden"); }
function closeDialog() { $("uploadDialog").classList.add("hidden"); }
$("uploadToggle").addEventListener("click", openDialog);
$("welcomeUpload").addEventListener("click", openDialog);
$("closeDialog").addEventListener("click", closeDialog);
$("uploadDialog").addEventListener("click", (e) => { if (e.target.id === "uploadDialog") closeDialog(); });
$("refreshLessons").addEventListener("click", loadLessons);
$("speed").addEventListener("change", () => { $("audioPlayer").playbackRate = Number($("speed").value); });

async function loadLessons() {
  try {
    const lessons = await api("/api/lessons");
    const list = $("lessonList"); list.replaceChildren();
    if (!lessons.length) {
      const empty = document.createElement("p"); empty.className = "empty-list"; empty.textContent = "No lessons yet"; list.append(empty); return;
    }
    lessons.forEach((lesson) => {
      const button = document.createElement("button"); button.className = "lesson-item";
      if (state.lesson?.id === lesson.id) button.classList.add("active");
      const name = document.createElement("strong"); name.textContent = lesson.title;
      const meta = document.createElement("small"); meta.textContent = `${lesson.stats.page_count} pages · ${fmt(lesson.duration)} audio`;
      button.append(name, meta); button.addEventListener("click", () => openLesson(lesson.id)); list.append(button);
    });
  } catch (error) { console.error(error); }
}

async function openLesson(id) {
  try {
    state.lesson = await api(`/api/lessons/${id}`);
    state.currentPage = null; state.manualPage = null; state.activeId = null; state.point = null; state.focusEvent = null;
    $("welcome").classList.add("hidden"); $("playerSection").classList.remove("hidden");
    $("lessonTitle").textContent = state.lesson.title;
    $("lessonMeta").textContent = `${state.lesson.pages.length} original pages · ${state.lesson.whiteboards.length} analogy pages · ${fmt(state.lesson.duration)} audio`;
    $("pointCount").textContent = `${state.lesson.points.length} points`;
    $("reviewSummary").textContent = `${state.lesson.stats.review_count} points and ${state.lesson.stats.equation_focus_review_count||0} equation cues need review. Select a point to correct its marks.`;
    const audio = $("audioPlayer");
    audio.src = `/api/lessons/${id}/audio`;
    audio.preservesPitch = true;
    audio.playbackRate = Number($("speed").value) || 1;
    $("editor").classList.add("hidden"); $("whiteboardEditor").classList.add("hidden"); $("exportStatus").classList.add("hidden");
    $("rebuildFocus").textContent = state.lesson.equation_focus?.length ? `Rebuild equation focus (${state.lesson.equation_focus.length})` : "Build equation focus";
    const selector=$("pageSelect"); selector.replaceChildren();
    state.lesson.lesson_pages.forEach((page,index)=>{const option=document.createElement("option");option.value=page.id;option.textContent=`${index+1}. ${page.kind==="whiteboard"?"Whiteboard":"PDF"} — ${page.title}`;selector.append(option);});
    drawTimeline(); renderAt(0); loadLessons();
    history.replaceState(null, "", `/?lesson=${encodeURIComponent(id)}`);
  } catch (error) { alert(`Could not open lesson: ${error.message}`); }
}

function drawTimeline() {
  const list = $("pointList"); list.replaceChildren();
  state.lesson.points.forEach((point) => {
    const item = document.createElement("div"); item.className = "point"; item.dataset.id = point.id;
    const top = document.createElement("div"); top.className = "point-top";
    const time = document.createElement("span"); time.className = "point-time"; time.textContent = `${fmt(point.start)}–${fmt(point.end)}`;
    const tag = document.createElement("span"); tag.className = "point-tag";
    if (point.needs_review) tag.classList.add("review");
    tag.textContent = point.needs_review ? "review" : point.action;
    top.append(time, tag);
    const title = document.createElement("div"); title.className = "point-title"; title.textContent = point.topic;
    const text = document.createElement("div"); text.className = "point-text"; text.textContent = point.text;
    item.append(top, title, text);
    item.addEventListener("click", () => selectPoint(point.id));
    list.append(item);
  });
}

function activePointAt(time) {
  const points = state.lesson?.points || [];
  for (let i = points.length - 1; i >= 0; i--) if (points[i].start <= time) return points[i];
  return null;
}

function activeCaptionAt(time) {
  const captions = state.lesson?.captions || [];
  for (let i = captions.length - 1; i >= 0; i--) {
    if (captions[i].start <= time) return captions[i];
  }
  return null;
}

function pageForTime(time) {
  const points = state.lesson?.points || [];
  for (let i = points.length - 1; i >= 0; i--) {
    if (points[i].start <= time) return points[i].page_id || `pdf-${points[i].page}`;
  }
  return "pdf-1";
}

function renderAt(time) {
  if (!state.lesson) return;
  const point = activePointAt(time);
  const caption = activeCaptionAt(time);
  const pageId = state.point && state.drawMode ? `pdf-${Number($("editPage").value)}` : (state.manualPage || pageForTime(time));
  showPage(pageId, time, Boolean(state.manualPage));
  $("nowTime").textContent = fmt(time);
  $("currentConcept").textContent = caption?.text || point?.concept || "Introduction";
  $("confidencePill").textContent = point ? (point.needs_review ? "Needs review" : "Grounded point") : "Waiting";
  $("confidencePill").classList.toggle("review", Boolean(point?.needs_review));
  const meta=state.lesson.lesson_pages.find((p)=>p.id===pageId);
  if(meta?.kind==="whiteboard"){
    $("confidencePill").textContent=meta.review_status==="pending"?"Review analogy":"Saved analogy";
    $("confidencePill").classList.toggle("review",meta.review_status==="pending");
  }
  if(state.manualPage&&meta?.kind==="whiteboard") $("currentConcept").textContent=meta.title;
  if(meta?.kind==="whiteboard") drawWhiteboard(meta, state.manualPage ? Infinity : time);
  else drawAnnotations(pageId, time, point);
  if (point?.id !== state.activeId) {
    state.activeId = point?.id || null;
    $("pointList").querySelectorAll(".point").forEach((item) => item.classList.toggle("active", item.dataset.id === state.activeId));
    const selected = $("pointList").querySelector(".point.active");
    if (selected && !state.point) selected.scrollIntoView({ block: "nearest" });
  }
}

function showPage(pageId) {
  if (!state.lesson) return;
  const meta=state.lesson.lesson_pages.find((p)=>p.id===pageId) || state.lesson.lesson_pages[0];
  if(pageId !== state.currentPage){
    state.currentPage=pageId; $("pageSelect").value=pageId;
    $("stage").classList.toggle("whiteboard-stage",meta.kind==="whiteboard");
    if(meta.kind==="pdf") $("pageImage").src=`/api/lessons/${state.lesson.id}/pages/${meta.source_page}`;
    const illustration=$("analogyImage");
    if(meta.kind==="whiteboard"&&meta.illustration){
      illustration.src=`/api/lessons/${state.lesson.id}/illustrations/${meta.illustration}`;
      illustration.classList.remove("hidden");
    } else { illustration.classList.add("hidden"); illustration.removeAttribute("src"); }
    positionAnnotationLayer();
  }
  $("pagePill").textContent=meta.kind==="whiteboard"?"Analogy page":`PDF ${meta.source_page}`;
  $("pageTitle").textContent=meta.title;
  $("resumeSync").classList.toggle("hidden",!state.manualPage);
}

function positionAnnotationLayer() {
  const img = $("pageImage"), stage = $("stage"), layer = $("annotationLayer");
  const meta=state.lesson?.lesson_pages.find((p)=>p.id===state.currentPage);
  if(meta?.kind==="whiteboard") { Object.assign(layer.style,{width:"100%",height:"100%",left:"0",top:"0"}); return; }
  if (!img.naturalWidth) return;
  const stageRect = stage.getBoundingClientRect();
  const ratio = Math.min(stageRect.width / img.naturalWidth, stageRect.height / img.naturalHeight);
  const width = img.naturalWidth * ratio, height = img.naturalHeight * ratio;
  Object.assign(layer.style, { width: `${width}px`, height: `${height}px`,
    left: `${(stageRect.width - width) / 2}px`, top: `${(stageRect.height - height) / 2}px` });
}
$("pageImage").addEventListener("load", () => { positionAnnotationLayer(); renderAt($("audioPlayer").currentTime || 0); });
window.addEventListener("resize", positionAnnotationLayer);

const svgNS = "http://www.w3.org/2000/svg";
function svgElement(name, attrs) {
  const el = document.createElementNS(svgNS, name);
  Object.entries(attrs).forEach(([key, value]) => el.setAttribute(key, String(value)));
  return el;
}
function drawAnnotations(pageId, time, active) {
  const svg = $("annotationLayer"); svg.replaceChildren();
  const focusedIds = new Set((state.lesson.equation_focus || []).map((event) => event.point_id));
  const focus = (state.lesson.equation_focus || []).find((event) => event.page_id === pageId && event.start <= time && time < event.end && !(state.lesson.grounding_version >= 2 && event.needs_review));
  const points = state.lesson.points.filter((p) => (p.page_id||`pdf-${p.page}`) === pageId && p.start <= time && p.region && ["underline", "box"].includes(p.action) && !(state.lesson.grounding_version >= 2 && p.needs_review) && !(p.action === "box" && (focusedIds.has(p.id) || focus)));
  const byRegion = new Map();
  points.forEach((point) => {
    const key = `${point.action}:${point.region.map((v) => Math.round(v * 200)).join(",")}`;
    byRegion.set(key, point);
  });
  const deduped = Array.from(byRegion.values());
  const latestId = deduped.length ? deduped[deduped.length - 1].id : null;
  const activeOnPage = active && (active.page_id || `pdf-${active.page}`) === pageId;
  deduped.forEach((point) => {
    const [x, y, w, h] = point.region.map((v) => v * 1000);
    const current = point.id === active?.id || (activeOnPage && active.action === "hold" && point.id === latestId);
    const color = "#f97316";
    let shape;
    if (point.action === "box") {
      shape = svgElement("rect", { x, y, width: w, height: h, fill: "none", stroke: color, "stroke-width": current ? 3.5 : 2.5 });
    } else {
      shape = svgElement("line", { x1: x, y1: y, x2: x + Math.max(w, 20), y2: y, stroke: color, "stroke-width": current ? 3.5 : 2.5, "stroke-linecap": "round" });
    }
    shape.setAttribute("opacity", current ? "1" : ".24");
    svg.append(shape);
  });
  const focusRegion = state.editingFocus && state.focusEvent?.page_id === pageId && state.draftFocusRegion ? state.draftFocusRegion : focus?.region;
  if (focusRegion && (!state.manualPage || state.editingFocus)) {
    const [x,y,w,h] = focusRegion.map((v) => v * 1000);
    svg.append(svgElement("rect",{x,y,width:w,height:h,fill:"none",stroke:"#f97316","stroke-width":3}));
  } else if (active?.reference_kind === "equation" && active?.action === "box" && focusedIds.has(active.id) && active.region && !state.manualPage) {
    const [x,y,w,h] = active.region.map((v) => v * 1000);
    svg.append(svgElement("rect",{x,y,width:w,height:h,fill:"none",stroke:"#f97316","stroke-width":3}));
  }
  if ((state.editingFocus ? state.draftFocusRegion : state.draftRegion) && state.drawMode) {
    const [x,y,w,h] = (state.editingFocus ? state.draftFocusRegion : state.draftRegion).map((v) => v * 1000);
    svg.append(svgElement("rect", { x, y, width: w, height: h, fill: "#f9731633", stroke: "#f97316", "stroke-width": 5, "stroke-dasharray": "12 7" }));
  }
}

function drawWhiteboard(board, time) {
  const svg=$("annotationLayer"); svg.replaceChildren();
  const title=svgElement("text",{x:45,y:58,fill:"#1c304e","font-size":32,"font-weight":700}); title.textContent=board.title; svg.append(title);
  const tag=svgElement("text",{x:45,y:88,fill:"#4b6487","font-size":15,"font-weight":700}); tag.textContent="ILLUSTRATIVE WHITEBOARD"; svg.append(tag);
  const elapsed=time===Infinity?Infinity:Math.max(0,time-board.start);
  (board.commands||[]).forEach((cmd)=>{
    if(elapsed<cmd.at)return; const progress=elapsed===Infinity?1:Math.min(1,(elapsed-cmd.at)/.8); let shape;
    const x=(v)=>90+v*820, y=(v)=>135+v*720;
    if(cmd.kind==="curve") shape=svgElement("polyline",{points:cmd.points.map(p=>`${x(p[0])},${y(p[1])}`).join(" "),fill:"none",stroke:"#1c304e","stroke-width":5,"stroke-linecap":"round","stroke-linejoin":"round"});
    else if(["line","arrow"].includes(cmd.kind)) shape=svgElement("line",{x1:x(cmd.x1||0),y1:y(cmd.y1||0),x2:x(cmd.x2||0),y2:y(cmd.y2||0),stroke:"#1c304e","stroke-width":5,"stroke-linecap":"round"});
    else if(cmd.kind==="point") shape=svgElement("circle",{cx:x(cmd.x||0),cy:y(cmd.y||0),r:10,fill:"#f97316"});
    else if(cmd.kind==="rectangle") shape=svgElement("rect",{x:x(cmd.x||0),y:y(cmd.y||0),width:(cmd.w||.2)*820,height:(cmd.h||.15)*720,fill:"none",stroke:"#1c304e","stroke-width":4});
    else if(cmd.kind==="ellipse") shape=svgElement("ellipse",{cx:x((cmd.x||0)+(cmd.w||.2)/2),cy:y((cmd.y||0)+(cmd.h||.15)/2),rx:(cmd.w||.2)*410,ry:(cmd.h||.15)*360,fill:"none",stroke:"#1c304e","stroke-width":4});
    if(shape){shape.style.opacity=progress;svg.append(shape);if(["line","curve","arrow"].includes(cmd.kind)){const length=shape.getTotalLength?.()||500;shape.style.strokeDasharray=length;shape.style.strokeDashoffset=length*(1-progress);}}
    if(cmd.label&&progress>.72){const label=svgElement("text",{x:x(cmd.x??cmd.x2??.5)+12,y:y(cmd.y??cmd.y2??.5)-18,fill:"#4b6487","font-size":20});label.textContent=cmd.label;svg.append(label);}
    if(cmd.kind==="text"&&progress>.2){const text=svgElement("text",{x:x(cmd.x||.2),y:y(cmd.y||.8),fill:"#1c304e","font-size":22});text.textContent=cmd.text||"";svg.append(text);}
  });
}

function openWhiteboardEditor(id) {
  const board=state.lesson.whiteboards.find((b)=>b.id===id);
  if(!board)return;
  state.editingBoard=id;
  state.boardSteps=structuredClone(board.commands||[]);
  state.removeImage=false;
  $("whiteboardTitle").value=board.title;
  $("whiteboardEvidence").textContent=board.evidence?`Spoken evidence: “${board.evidence}”`:"Review the audio and source page before approving.";
  $("approveWhiteboard").checked=board.review_status==="approved";
  $("removeAnalogyImage").classList.toggle("hidden",!board.illustration);
  $("whiteboardStatus").textContent=board.illustration_error||"Drawing stages are measured from the start of this analogy.";
  $("whiteboardEditor").classList.remove("hidden");
  renderWhiteboardSteps();
}

function renderWhiteboardSteps(){
  const list=$("whiteboardSteps");list.replaceChildren();
  state.boardSteps.forEach((cmd,index)=>{
    const row=document.createElement("div");row.className="whiteboard-step";
    const heading=document.createElement("div");heading.className="whiteboard-step-head";
    const name=document.createElement("strong");name.textContent=`${index+1}. ${cmd.kind}`;
    const remove=document.createElement("button");remove.type="button";remove.textContent="Remove";
    remove.addEventListener("click",()=>{state.boardSteps.splice(index,1);renderWhiteboardSteps();});
    heading.append(name,remove);row.append(heading);
    const fields=["at",cmd.kind==="text"?"text":"label"];
    if(["point","text","rectangle","ellipse"].includes(cmd.kind))fields.push("x","y");
    if(["rectangle","ellipse"].includes(cmd.kind))fields.push("w","h");
    if(["line","arrow"].includes(cmd.kind))fields.push("x1","y1","x2","y2");
    if(cmd.kind==="curve")fields.push("points");
    fields.forEach((key)=>{
      const label=document.createElement("label");label.textContent=key==="at"?"Seconds":key;
      const input=document.createElement("input");input.type=key==="at"||["x","y","x1","y1","x2","y2","w","h"].includes(key)?"number":"text";
      if(input.type==="number"){input.step="0.01";input.min="0";input.max=key==="at"?String(Math.max(.1,state.lesson.whiteboards.find(b=>b.id===state.editingBoard).end-state.lesson.whiteboards.find(b=>b.id===state.editingBoard).start)):"1";}
      input.value=key==="points"?JSON.stringify(cmd.points||[]):String(cmd[key]??"");
      input.addEventListener("input",()=>{
        if(key==="points") {try{cmd.points=JSON.parse(input.value);}catch(_){cmd.points=null;}}
        else cmd[key]=input.type==="number"?Number(input.value):input.value;
      });
      label.append(input);row.append(label);
    });
    list.append(row);
  });
}

$("closeWhiteboardEditor").addEventListener("click",()=>{$("whiteboardEditor").classList.add("hidden");state.editingBoard=null;});
$("removeAnalogyImage").addEventListener("click",()=>{
  state.removeImage=true;$("approveWhiteboard").checked=false;
  $("whiteboardStatus").textContent="The illustration will be removed when you save. Drawing steps remain.";
  $("removeAnalogyImage").classList.add("hidden");
});
$("addWhiteboardStep").addEventListener("click",()=>{
  if(state.boardSteps.length>=12){$("whiteboardStatus").textContent="The lesson supports up to 12 steps per analogy.";return;}
  const kind=$("newWhiteboardStep").value;
  const base={kind,at:0,x:.5,y:.5,label:""};
  if(["line","arrow"].includes(kind))Object.assign(base,{x1:.3,y1:.5,x2:.7,y2:.5});
  if(["rectangle","ellipse"].includes(kind))Object.assign(base,{x:.3,y:.3,w:.4,h:.3});
  if(kind==="text")base.text="New label";
  state.boardSteps.push(base);renderWhiteboardSteps();
});
$("saveWhiteboard").addEventListener("click",async()=>{
  if(!state.editingBoard)return;
  const id=state.editingBoard;
  try{
    const board=await api(`/api/lessons/${state.lesson.id}/whiteboards/${id}`,{
      method:"PATCH",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({title:$("whiteboardTitle").value,commands:state.boardSteps,
        approved:$("approveWhiteboard").checked,remove_illustration:state.removeImage})
    });
    const bi=state.lesson.whiteboards.findIndex(b=>b.id===id);
    const pi=state.lesson.lesson_pages.findIndex(p=>p.id===id);
    state.lesson.whiteboards[bi]=board;state.lesson.lesson_pages[pi]=board;
    state.removeImage=false;
    $("removeAnalogyImage").classList.toggle("hidden",!board.illustration);
    state.lesson.points.filter(p=>p.whiteboard_id===id).forEach(p=>p.needs_review=board.review_status!=="approved");
    state.lesson.stats.review_count=state.lesson.points.filter(p=>p.needs_review).length;
    $("reviewSummary").textContent=`${state.lesson.stats.review_count} points need review. Select any point to correct its page or mark.`;
    $("whiteboardStatus").textContent="Saved. The lesson and next MP4 export use these stages.";
    state.currentPage=null;drawTimeline();renderAt($("audioPlayer").currentTime);
  }catch(error){$("whiteboardStatus").textContent=`Could not save: ${error.message}`;}
});

function selectPoint(id) {
  const point = state.lesson.points.find((p) => p.id === id);
  if (!point) return;
  state.point = point;
  $("audioPlayer").pause(); $("audioPlayer").currentTime = point.start + .05;
  state.manualPage=null;
  if(point.action==="whiteboard") { $("editor").classList.add("hidden"); openWhiteboardEditor(point.whiteboard_id); renderAt($("audioPlayer").currentTime); return; }
  $("whiteboardEditor").classList.add("hidden");
  $("editor").classList.remove("hidden");
  $("editPage").value = point.page;
  $("editPage").max = state.lesson.pages.length;
  populateBlocks(point.page,point.block_id);
  $("editAction").value = ["hold", "underline", "box"].includes(point.action) ? point.action : "hold";
  $("editConcept").value = point.concept;
  state.draftRegion = point.region ? [...point.region] : null;
  state.focusEvent = null; state.editingFocus = false; state.draftFocusRegion = null;
  const cues = (state.lesson.equation_focus || []).filter((event) => event.point_id === point.id);
  $("focusEditor").classList.toggle("hidden", !cues.length);
  const picker = $("focusSelect"); picker.replaceChildren();
  cues.forEach((event) => { const option=document.createElement("option"); option.value=event.id; option.textContent=`${fmt(event.start)}.${String(Math.round((event.start%1)*100)).padStart(2,"0")} — ${event.term_id}`; picker.append(option); });
  if (cues.length) selectFocus(cues[0].id);
  const matched=(state.lesson.document_blocks?.[String(point.page)]||[]).find(b=>b.id===point.block_id);
  $("regionStatus").textContent = matched ? `Matched ${matched.kind}: ${matched.text.slice(0,140)}` : (point.region ? "Current target is shown. Draw again to change it." : "No verified block. Select one or draw a target manually.");
  renderAt($("audioPlayer").currentTime);
}
function populateBlocks(page,id){
  const picker=$("editBlock");picker.replaceChildren();
  const empty=document.createElement("option");empty.value="";empty.textContent="Choose a block";picker.append(empty);
  (state.lesson.document_blocks?.[String(page)]||[]).forEach(block=>{
    const option=document.createElement("option");option.value=block.id;
    option.textContent=`${block.kind}: ${block.text.slice(0,90)}`;picker.append(option);
  });picker.value=id||"";
}
$("editPage").addEventListener("change",()=>populateBlocks(Number($("editPage").value),null));
$("editBlock").addEventListener("change",()=>{
  const page=Number($("editPage").value);
  const block=(state.lesson.document_blocks?.[String(page)]||[]).find(b=>b.id===$("editBlock").value);
  if(!block)return;
  const [x,y,w,h]=block.region;
  if(block.kind==="equation"||block.kind==="figure"){
    $("editAction").value="box";state.draftRegion=[x,y,w,h];
  }else{
    $("editAction").value="underline";state.draftRegion=[x,Math.min(.99,y+h+.003),w,.006];
  }
  $("regionStatus").textContent=`Selected ${block.kind}: ${block.text.slice(0,140)}. Adjust the mark if needed.`;
  state.manualPage=`pdf-${page}`;renderAt($("audioPlayer").currentTime);
});
function selectFocus(id) {
  const event=(state.lesson.equation_focus||[]).find((row)=>row.id===id);
  if(!event)return;
  state.focusEvent=event; state.draftFocusRegion=[...event.region]; state.editingFocus=true;
  $("focusStart").value=event.start; $("focusEnd").value=event.end;
  const terms=$("focusTerm");terms.replaceChildren();
  const equation=(state.lesson.equations?.[String(event.page)]||[]).find((row)=>row.id===event.equation_id);
  [...(equation?.terms||[]),{id:"whole",label:"Whole equation"}].forEach((term)=>{const option=document.createElement("option");option.value=term.id;option.textContent=term.label;terms.append(option);});
  terms.value=event.term_id;
  $("focusStatus").textContent=`Heard “${event.evidence}” · confidence ${Math.round(event.confidence*100)}%.`;
}
$("focusSelect").addEventListener("change",()=>{selectFocus($("focusSelect").value);renderAt($("audioPlayer").currentTime);});
$("focusTerm").addEventListener("change",()=>{
  const event=state.focusEvent;if(!event)return;
  const equation=(state.lesson.equations?.[String(event.page)]||[]).find((row)=>row.id===event.equation_id);
  const term=(equation?.terms||[]).find((row)=>row.id===$("focusTerm").value);
  state.draftFocusRegion=[...(term?.region||equation?.region||event.region)];renderAt($("audioPlayer").currentTime);
});
$("drawFocusRegion").addEventListener("click",()=>{if(!state.focusEvent)return;state.drawMode=true;state.editingFocus=true;$("editHint").classList.remove("hidden");$("focusStatus").textContent="Drag over the equation term, then save the cue.";});
$("saveFocus").addEventListener("click",async()=>{
  const event=state.focusEvent;if(!event)return;
  try{
    const updated=await api(`/api/lessons/${state.lesson.id}/equation-focus/${event.id}`,{method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify({start:Number($("focusStart").value),end:Number($("focusEnd").value),term_id:$("focusTerm").value,region:state.draftFocusRegion,needs_review:false})});
    const index=state.lesson.equation_focus.findIndex((row)=>row.id===updated.id);state.lesson.equation_focus[index]=updated;
    state.lesson.equation_focus.sort((a,b)=>a.start-b.start);state.focusEvent=updated;
    $("focusStatus").textContent="Saved. Browser playback and the next MP4 use this cue.";renderAt($("audioPlayer").currentTime);
  }catch(error){$("focusStatus").textContent=`Save failed: ${error.message}`;}
});
$("rebuildFocus").addEventListener("click",async()=>{
  if(!state.lesson)return;
  const button=$("rebuildFocus");button.disabled=true;button.textContent="Mapping words to equations…";
  try{const result=await api(`/api/lessons/${state.lesson.id}/equation-focus/rebuild`,{method:"POST"});
    state.lesson.equation_focus=result.events;
    const refreshed=await api(`/api/lessons/${state.lesson.id}`);state.lesson.equations=refreshed.equations;
    button.textContent=`Rebuild equation focus (${result.count})`;
    if(state.point)selectPoint(state.point.id);renderAt($("audioPlayer").currentTime);
  }catch(error){button.textContent="Build equation focus";alert(`Equation mapping failed: ${error.message}`);}finally{button.disabled=false;}
});
$("rebuildAlignment").addEventListener("click",async()=>{
  if(!state.lesson)return;
  const id=state.lesson.id,button=$("rebuildAlignment");button.disabled=true;button.textContent="Rebuilding locally…";
  try{
    await api(`/api/lessons/${id}/alignment/rebuild`,{method:"POST"});
    let result;
    for(let i=0;i<300;i++){
      await new Promise(resolve=>setTimeout(resolve,1000));
      result=await api(`/api/jobs/${id}`);
      if(result.status==="ready"||result.status==="failed")break;
    }
    if(result?.status!=="ready")throw new Error(result?.message||"The rebuild has not completed. Refresh the lesson later.");
    const second=$("audioPlayer").currentTime;
    await openLesson(id);$("audioPlayer").currentTime=second;renderAt(second);
  }catch(error){alert(`PDF grounding failed: ${error.message}`);}
  finally{button.disabled=false;button.textContent="Rebuild PDF grounding";}
});
$("closeEditor").addEventListener("click", () => { state.point=null; state.focusEvent=null; state.editingFocus=false; state.drawMode=false; state.draftRegion=null; $("editor").classList.add("hidden"); $("editHint").classList.add("hidden"); renderAt($("audioPlayer").currentTime); });
$("drawRegion").addEventListener("click", () => {
  if (!state.point) return;
  const page = Number($("editPage").value);
  state.drawMode = true; $("editHint").classList.remove("hidden");
  state.editingFocus=false;
  renderAt($("audioPlayer").currentTime);
  $("regionStatus").textContent = "Drag on the visible PDF page. The selection will be saved with this point.";
});

function normalizedPointer(event) {
  const rect = $("annotationLayer").getBoundingClientRect();
  return [Math.max(0,Math.min(1,(event.clientX-rect.left)/rect.width)),Math.max(0,Math.min(1,(event.clientY-rect.top)/rect.height))];
}
$("stage").addEventListener("pointerdown", (event) => {
  if (!state.drawMode) return;
  event.preventDefault(); state.drag = normalizedPointer(event); $("stage").setPointerCapture(event.pointerId);
});
$("stage").addEventListener("pointermove", (event) => {
  if (!state.drag) return;
  const [x,y]=normalizedPointer(event), [sx,sy]=state.drag;
  const region=[Math.min(x,sx),Math.min(y,sy),Math.max(Math.abs(x-sx),.015),Math.max(Math.abs(y-sy),.015)];
  if(state.editingFocus)state.draftFocusRegion=region;else state.draftRegion=region;
  drawAnnotations(state.currentPage,$("audioPlayer").currentTime,activePointAt($("audioPlayer").currentTime));
});
$("stage").addEventListener("pointerup", () => {
  if (!state.drag) return;
  state.drag=null; state.drawMode=false; $("editHint").classList.add("hidden");
  $(state.editingFocus ? "focusStatus" : "regionStatus").textContent="Region selected. Save the correction to keep it.";
});

$("saveEdit").addEventListener("click", async () => {
  if (!state.point) return;
  const patch = { page:Number($("editPage").value), block_id:$("editBlock").value||null, action:$("editAction").value,
    concept:$("editConcept").value.trim(), region:state.draftRegion, needs_review:false };
  if (["underline","box"].includes(patch.action) && !patch.region) {
    $("regionStatus").textContent="Draw a target region before saving this visual action."; return;
  }
  try {
    const updated = await api(`/api/lessons/${state.lesson.id}/points/${state.point.id}`,{
      method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify(patch)
    });
    const index=state.lesson.points.findIndex((p)=>p.id===updated.id); state.lesson.points[index]=updated;
    state.point=updated; state.lesson.stats.review_count=state.lesson.points.filter((p)=>p.needs_review).length;
    $("reviewSummary").textContent=`${state.lesson.stats.review_count} points need review. Select any point to correct its page or mark.`;
    drawTimeline(); $("regionStatus").textContent="Saved. This correction is now part of the lesson.";
    renderAt($("audioPlayer").currentTime);
  } catch(error) { $("regionStatus").textContent=`Save failed: ${error.message}`; }
});

$("audioPlayer").addEventListener("timeupdate",()=>renderAt($("audioPlayer").currentTime));
$("audioPlayer").addEventListener("seeked",()=>renderAt($("audioPlayer").currentTime));
$("audioPlayer").addEventListener("play",()=>{state.editingFocus=false;state.drawMode=false;renderAt($("audioPlayer").currentTime);});
// timeupdate is too coarse for individual spoken terms. Read the audio clock
// each frame, but redraw only when the active equation cue changes.
let lastFocusId=null,lastCaptionStart=null,lastPointId=null;
function followAudioWords(){
  const audio=$("audioPlayer");
  if(state.lesson&&!audio.paused&&!state.manualPage){
    const time=audio.currentTime;
    const cue=(state.lesson.equation_focus||[]).find((event)=>event.start<=time&&time<event.end);
    const id=cue?.id||null;
    const capStart=activeCaptionAt(time)?.start??null;
    const ptId=activePointAt(time)?.id||null;
    if(id!==lastFocusId||capStart!==lastCaptionStart||ptId!==lastPointId){
      lastFocusId=id;lastCaptionStart=capStart;lastPointId=ptId;
      renderAt(time);
    }
  }
  requestAnimationFrame(followAudioWords);
}
requestAnimationFrame(followAudioWords);
function browsePage(offset=0, selected=null){
  if(!state.lesson)return; const pages=state.lesson.lesson_pages; const current=pages.findIndex((p)=>p.id===(state.manualPage||state.currentPage));
  const next=selected||pages[Math.max(0,Math.min(pages.length-1,current+offset))].id;
  $("audioPlayer").pause(); state.manualPage=next; state.point=null; state.focusEvent=null; state.editingFocus=false; $("editor").classList.add("hidden");
  const page=pages.find(p=>p.id===next);
  if(page?.kind==="whiteboard")openWhiteboardEditor(next);else $("whiteboardEditor").classList.add("hidden");
  renderAt($("audioPlayer").currentTime);
}
function skipBy(delta){
  if(!state.lesson)return;
  const audio=$("audioPlayer");
  const maxTime=Number.isFinite(audio.duration)?audio.duration:(state.lesson.duration||Infinity);
  const nextTime=Math.max(0,Math.min(maxTime,(audio.currentTime||0)+delta));
  state.manualPage=null;
  audio.currentTime=nextTime;
  renderAt(nextTime);
}
$("skipBack").addEventListener("click",(e)=>{e.currentTarget.blur();skipBy(-5);});
$("skipForward").addEventListener("click",(e)=>{e.currentTarget.blur();skipBy(5);});
function togglePlayPause(){
  if(!state.lesson)return;
  const audio=$("audioPlayer");
  if(audio.paused){
    state.manualPage=null;
    audio.play().catch(()=>{});
  }else{
    audio.pause();
  }
}
function syncFullscreenUi(){
  const viewer=$("viewerColumn");
  const btn=$("fullscreenToggle");
  if(!viewer||!btn)return;
  const active=document.fullscreenElement===viewer||document.webkitFullscreenElement===viewer||viewer.classList.contains("is-fullscreen");
  btn.textContent=active?"✕ Exit full screen":"⛶ Full screen";
  btn.title=active?"Exit full screen (F or Esc)":"Full screen (F)";
  requestAnimationFrame(()=>{
    positionAnnotationLayer();
    if(state.lesson)renderAt($("audioPlayer").currentTime||0);
  });
  setTimeout(()=>{
    positionAnnotationLayer();
    if(state.lesson)renderAt($("audioPlayer").currentTime||0);
  },120);
}
async function toggleFullscreen(){
  if(!state.lesson)return;
  const viewer=$("viewerColumn");
  if(!viewer)return;
  const isNativeFs=document.fullscreenElement===viewer||document.webkitFullscreenElement===viewer;
  if(isNativeFs){
    if(document.exitFullscreen)await document.exitFullscreen().catch(()=>{});
    else if(document.webkitExitFullscreen)document.webkitExitFullscreen();
    viewer.classList.remove("is-fullscreen");
  }else if(viewer.classList.contains("is-fullscreen")){
    viewer.classList.remove("is-fullscreen");
  }else if(viewer.requestFullscreen){
    try{await viewer.requestFullscreen();}catch{viewer.classList.add("is-fullscreen");}
  }else if(viewer.webkitRequestFullscreen){
    try{viewer.webkitRequestFullscreen();}catch{viewer.classList.add("is-fullscreen");}
  }else{
    viewer.classList.add("is-fullscreen");
  }
  syncFullscreenUi();
}
$("fullscreenToggle")?.addEventListener("click",(e)=>{e.currentTarget.blur();toggleFullscreen();});
$("stage")?.addEventListener("dblclick",(event)=>{
  if(state.drawMode||event.target.closest("button,select,input"))return;
  toggleFullscreen();
});
document.addEventListener("fullscreenchange",syncFullscreenUi);
document.addEventListener("webkitfullscreenchange",syncFullscreenUi);
window.addEventListener("keydown",(event)=>{
  if(!state.lesson||event.altKey||event.ctrlKey||event.metaKey)return;
  if(!$("uploadDialog").classList.contains("hidden"))return;
  const el=document.activeElement;
  const tag=el?.tagName;
  const isTextInput=(tag==="INPUT"&&!["button","checkbox","radio","range"].includes((el.type||"").toLowerCase()))||tag==="TEXTAREA"||el?.isContentEditable;
  if(isTextInput)return;
  if(event.code==="Space"||event.key===" "){
    if(tag==="AUDIO"||tag==="BUTTON")el.blur();
    event.preventDefault();
    event.stopPropagation();
    togglePlayPause();
  }else if(event.key==="ArrowLeft"){
    if(tag==="AUDIO"||tag==="BUTTON")el.blur();
    event.preventDefault();
    event.stopPropagation();
    skipBy(-5);
  }else if(event.key==="ArrowRight"){
    if(tag==="AUDIO"||tag==="BUTTON")el.blur();
    event.preventDefault();
    event.stopPropagation();
    skipBy(5);
  }else if(event.key==="f"||event.key==="F"){
    if(tag==="SELECT")return;
    if(tag==="AUDIO"||tag==="BUTTON")el.blur();
    event.preventDefault();
    event.stopPropagation();
    toggleFullscreen();
  }else if(event.key==="Escape"&&$("viewerColumn")?.classList.contains("is-fullscreen")){
    $("viewerColumn").classList.remove("is-fullscreen");
    syncFullscreenUi();
  }
},{capture:true});
window.addEventListener("keyup",(event)=>{
  if(!state.lesson||!$("uploadDialog").classList.contains("hidden"))return;
  const el=document.activeElement;
  const tag=el?.tagName;
  const isTextInput=(tag==="INPUT"&&!["button","checkbox","radio","range"].includes((el.type||"").toLowerCase()))||tag==="TEXTAREA"||el?.isContentEditable;
  if(isTextInput)return;
  if(event.code==="Space"||event.key===" "){
    event.preventDefault();
    event.stopPropagation();
  }
},{capture:true});
$("prevPage").addEventListener("click",()=>browsePage(-1));
$("nextPage").addEventListener("click",()=>browsePage(1));
$("pageSelect").addEventListener("change",()=>browsePage(0,$("pageSelect").value));
$("resumeSync").addEventListener("click",()=>{state.manualPage=null;renderAt($("audioPlayer").currentTime);});
$("exportJson").addEventListener("click",()=>{
  if (!state.lesson) return;
  window.open(`/api/lessons/${state.lesson.id}/export.json`,"_blank","noopener");
});
$("exportMp4").addEventListener("click",async()=>{
  if(!state.lesson)return; const button=$("exportMp4"), status=$("exportStatus");
  button.disabled=true; status.classList.remove("hidden"); status.textContent="Starting compact MP4 export…";
  try{
    const job=await api(`/api/lessons/${state.lesson.id}/exports`,{method:"POST"});
    let current=job;
    while(current.status!=="ready"){
      if(current.status==="failed")throw new Error(current.message);
      status.textContent=current.message; await new Promise((resolve)=>setTimeout(resolve,1800));
      current=await api(`/api/exports/${job.job_id}`);
    }
    const mb=(current.size_bytes/1048576).toFixed(1); status.replaceChildren(document.createTextNode(`MP4 ready · ${mb} MB`));
    const link=document.createElement("a");link.href=current.download_url;link.textContent="Download";status.append(link);
  }catch(error){status.textContent=`MP4 export failed: ${error.message}`;}finally{button.disabled=false;}
});

$("uploadForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const status=$("uploadStatus"); status.classList.remove("hidden"); status.textContent="Uploading files…";
  const button=$("uploadForm").querySelector("button[type=submit]"); button.disabled=true;
  try {
    const form=new FormData($("uploadForm"));
    if (!form.get("transcript")?.size) form.delete("transcript");
    const created=await api("/api/lessons",{method:"POST",body:form});
    status.textContent="Files uploaded. Preparing lesson…";
    await pollJob(created.id,status);
    closeDialog(); $("uploadForm").reset(); await openLesson(created.id);
  } catch(error) { status.textContent=`Could not create lesson: ${error.message}`; }
  finally { button.disabled=false; }
});

async function pollJob(id,status) {
  for (;;) {
    await new Promise((resolve)=>setTimeout(resolve,1800));
    const job=await api(`/api/jobs/${id}`);
    status.textContent=job.message;
    if(job.status==="ready") return;
    if(job.status==="failed") throw new Error(job.message);
  }
}

loadLessons().then(async()=>{
  const id=new URLSearchParams(location.search).get("lesson");
  if(id) await openLesson(id);
  else {
    const lessons=await api("/api/lessons");
    if(lessons.length) await openLesson(lessons[0].id);
  }
}).catch(console.error);
