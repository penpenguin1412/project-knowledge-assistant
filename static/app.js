const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const state = {project: null, page: 'ask', token: '', documents: [], tasks: [], drafts: [], runs: [], busy: false};
const labels = {ask:'资料问答', documents:'项目资料', meetings:'会议转待办', tasks:'待办清单', runs:'运行记录'};
const statuses = {todo:'待开始', doing:'进行中', done:'已完成', cancelled:'已取消'};

function notice(message, error = false) {
  $('#notice').hidden = false;
  $('#notice').textContent = message;
  $('#notice').className = error ? 'error' : '';
}
async function api(path, {method='GET', body} = {}) {
  const headers = {'X-Assistant-Request':'1'};
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  if (body && !(body instanceof FormData)) {headers['Content-Type']='application/json';body=JSON.stringify(body);}
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 65000);
  try {
    const response = await fetch(`/api${path}`, {method,headers,body,signal:controller.signal});
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '输入不符合要求，请检查必填内容、长度和日期。');
    return data;
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('请求超时。结果可能已保存，请刷新检查，避免重复调用模型。');
    throw error;
  } finally {clearTimeout(timer);}
}
async function work(fn, message='处理中…') {
  if (state.busy) return;
  state.busy = true;
  const controls = [...document.querySelectorAll('button,input,select,textarea')].filter(c => !c.disabled);
  controls.forEach(c => c.disabled = true);
  document.body.setAttribute('aria-busy','true');
  notice(message);
  try {await fn();} catch (error) {notice(error.message || '服务连接失败，请确认后端已启动。', true);}
  finally {state.busy = false;controls.forEach(c => c.disabled = false);document.body.removeAttribute('aria-busy');}
}
function projectPath(suffix) {
  if (!state.project) throw new Error('请先创建项目，或载入示例项目。');
  return `/projects/${state.project}${suffix}`;
}
function showPage(page) {
  state.page=page;
  $('#notice').hidden=true;
  document.querySelectorAll('.page').forEach(el=>el.hidden=el.id!==page);
  document.querySelectorAll('.nav').forEach(el=>{el.classList.toggle('active',el.dataset.page===page);if(el.dataset.page===page)el.setAttribute('aria-current','page');else el.removeAttribute('aria-current');});
  $('#page-title').textContent=labels[page];
}
function empty(text) {return `<div class="empty"><h3>${esc(text)}</h3></div>`;}
async function loadProjects(selected) {
  const rows=await api('/projects');
  state.project = rows.some(p=>p.id===Number(selected)) ? Number(selected) : (rows[0]?.id || null);
  $('#project').innerHTML = rows.length ? rows.map(p=>`<option value="${p.id}">${esc(p.name)}</option>`).join('') : '<option value="">请选择项目</option>';
  if(state.project) $('#project').value=state.project;
  await refresh();
}
async function refresh() {
  if (state.project) {
    [state.documents,state.tasks,state.drafts,state.runs]=await Promise.all(['documents','tasks','drafts','runs'].map(s=>api(projectPath('/'+s))));
  } else {state.documents=[];state.tasks=[];state.drafts=[];state.runs=[];}
  $('#doc-count').textContent=state.documents.filter(d=>!d.archived).length;
  $('#task-count').textContent=state.tasks.filter(t=>['todo','doing'].includes(t.status)).length;
  renderDocuments();renderTasks();renderDrafts();renderRuns();
}
function renderDocuments() {
  $('#document-list').innerHTML=state.documents.length ? state.documents.map(d=>`<article class="card doc-row"><div><strong>${esc(d.name)}</strong><p>${d.chunk_count} 个片段 · ${d.archived?'已归档，不参与检索':'可检索'} · ${esc(new Date(d.created_at).toLocaleDateString())}</p></div><div class="doc-actions"><button data-source="${d.id}">查看原文</button><button data-archive="${d.id}">${d.archived?'恢复检索':'归档'}</button></div></article>`).join('') : empty('还没有资料，先导入一份项目文档');
}
function fields(item, prefix) {
  return `<div class="task-fields"><label for="${prefix}-title">待办事项<input id="${prefix}-title" name="title" required maxlength="240" value="${esc(item.title)}"></label><label for="${prefix}-owner">负责人 · 可待定<input id="${prefix}-owner" name="owner" maxlength="80" placeholder="待定" value="${esc(item.owner)}"></label><label for="${prefix}-date">截止日期 · 可待定<input id="${prefix}-date" name="due_date" type="date" value="${esc(item.due_date||'')}"></label></div>`;
}
function renderDrafts() {
  $('#draft-list').innerHTML=state.drafts.length ? state.drafts.map(d=>`<form class="card draft-form" data-draft="${d.id}"><div class="answer-header"><h3>待确认草稿</h3><span class="badge">${d.mode==='llm'?'真实模型提取':'明确格式规则'}</span></div><p>只勾选需要创建的事项；空负责人和空日期表示待定。人工修改不会改写原文。</p>${d.items.length?d.items.map((item,i)=>`<article class="draft-item" data-index="${i}"><label class="select-task"><input type="checkbox" name="selected" checked>创建这项待办</label>${fields(item,`d-${d.id}-${i}`)}<div class="citation"><small>提取依据</small><p>${esc(item.quote)}</p></div></article>`).join(''):'<p>未识别到待办。规则模式只接受指定格式；自由文本可切换真实模型。</p>'}<details><summary>查看完整会议原文</summary><pre>${esc(d.source)}</pre></details>${d.items.length?'<div class="form-footer"><label class="select-task"><input type="checkbox" name="confirmed" required>我已核对所选事项、负责人和日期</label><button class="primary">确认并创建待办</button></div>':''}</form>`).join('') : empty('提取结果会保存在这里，刷新后仍可继续确认');
}
function renderTasks() {
  const filter=$('#task-filter').value;
  const today=new Date();const localDate=`${today.getFullYear()}-${String(today.getMonth()+1).padStart(2,'0')}-${String(today.getDate()).padStart(2,'0')}`;
  const rows=state.tasks.filter(t=>filter==='all'||t.status===filter);
  $('#task-list').innerHTML=rows.length ? rows.map(t=>`<form class="card task-form" data-task="${t.id}"><div class="answer-header"><h3>#${t.id} · ${statuses[t.status]}</h3>${t.due_date&&t.due_date<localDate&&['todo','doing'].includes(t.status)?'<span class="overdue">已逾期</span>':''}</div>${fields(t,`t-${t.id}`)}<details class="citation"><summary>查看原始提取依据</summary><p>${esc(t.quote)}</p></details><div class="form-footer"><label>状态 <select name="status">${Object.entries(statuses).map(([v,l])=>`<option value="${v}" ${t.status===v?'selected':''}>${l}</option>`).join('')}</select></label><button class="secondary">保存修改</button></div></form>`).join('') : empty(filter==='all'?'尚无待办，请先提取会议记录并人工确认':'没有符合此状态的待办');
}
function renderRuns() {
  $('#run-list').innerHTML=state.runs.length ? state.runs.map(r=>`<article class="card"><div class="answer-header"><strong>${r.kind==='question'?'资料提问':'会议提取'} · ${esc(r.mode)}</strong><span class="meta">${r.elapsed_ms} ms</span></div><p>${esc(new Date(r.created_at).toLocaleString())} · ${r.output.error?'调用失败':r.output.abstained?'资料不足 / 拒答':'已返回结果'}</p><details><summary>${esc(r.input.slice(0,110))}</summary><pre>${esc(JSON.stringify({input:r.input,...r.output},null,2))}</pre></details></article>`).join('') : empty('暂无运行记录，完成一次问答或提取后在此查看');
}
function renderAnswer(result) {
  const citations=result.citations.length?result.citations:result.candidates||[];
  $('#answer').innerHTML=`<article class="card"><div class="answer-header"><span class="badge">${result.mode==='llm'?'真实模型问答':'原文检索 · 非模型回答'}</span><span class="meta">${result.elapsed_ms} ms · ${esc(result.run_id.slice(0,8))}</span></div><p class="answer-text">${esc(result.answer)}</p>${result.mode==='llm'&&!result.abstained?'<p class="meta">已校验引用来自候选原文；回答与原文的语义一致性仍需人工核对。</p>':''}${result.abstained&&citations.length?'<h3>仅供核对的候选原文</h3>':''}${citations.map((c,i)=>`<div class="citation"><small>[${i+1}] ${esc(c.document_name)} · ${esc(c.location)} · 检索分 ${c.score}</small><p>${esc(c.quote||c.text)}</p><button data-source="${c.document_id}" data-chunk="${c.id}">展开资料原文 ↗</button></div>`).join('')}</article>`;
}
async function openSource(id, chunk) {
  const d=await api(projectPath(`/documents/${id}`));
  $('#source-title').textContent=d.name;
  $('#source-content').innerHTML=d.chunks.map(c=>`<article id="chunk-${c.id}" class="${Number(chunk)===c.id?'highlight':''}"><small class="meta">${esc(c.location)}</small><div>${esc(c.text)}</div></article>`).join('');
  $('#source-dialog').showModal();
  if(chunk) $(`#chunk-${chunk}`)?.scrollIntoView({block:'center'});
  notice('已打开资料原文。');
}
document.addEventListener('click', event=>{
  const button=event.target.closest('button');if(!button || state.busy)return;
  if(button.dataset.page)showPage(button.dataset.page);
  if(button.dataset.question){$('#question').value=button.dataset.question;$('#question').focus();}
  if(button.dataset.source)work(()=>openSource(Number(button.dataset.source),button.dataset.chunk),'读取原文…');
  if(button.dataset.archive)work(async()=>{const d=state.documents.find(d=>d.id===Number(button.dataset.archive));await api(projectPath(`/documents/${d.id}`),{method:'PATCH',body:{archived:!d.archived}});await refresh();notice(d.archived?'资料已恢复检索。':'资料已归档；历史引用仍保留。');});
});
$('#project').addEventListener('change',()=>work(async()=>{state.project=Number($('#project').value);$('#answer').innerHTML=empty('已切换项目，请重新提问');$('#meeting-text').value='';await refresh();notice('已切换项目。');}));
$('#create-project').addEventListener('submit',e=>{e.preventDefault();const name=$('#project-name').value;work(async()=>{const p=await api('/projects',{method:'POST',body:{name}});await loadProjects(p.id);$('#project-name').value='';$('#answer').innerHTML=empty('新项目已创建，请导入资料');$('#meeting-text').value='';showPage('documents');notice('项目已创建。');});});
$('#demo').addEventListener('click',()=>work(async()=>{const p=await api('/demo',{method:'POST'});await loadProjects(p.id);showPage('ask');$('#answer').innerHTML=empty('示例资料已就绪，试着问：项目交付物有哪些？');notice('已载入自编虚构资料，不含真实人员或合同信息。');}));
$('#upload-form').addEventListener('submit',e=>{e.preventDefault();const file=$('#file').files[0];work(async()=>{if(!file||file.size>5*1024*1024)throw new Error('请选择小于 5 MB 的文件。');const form=new FormData();form.append('file',file);await api(projectPath('/documents'),{method:'POST',body:form});$('#file').value='';await refresh();notice('资料导入成功，已建立可检索的原文片段。');},'解析文件并建立索引…');});
$('#ask-form').addEventListener('submit',e=>{e.preventDefault();const body={question:$('#question').value,mode:$('#qa-mode').value};work(async()=>{$('#answer').innerHTML=empty('正在查找依据…');try {const result=await api(projectPath('/ask'),{method:'POST',body});renderAnswer(result);await refresh();notice(result.abstained?'资料不足或引用校验未通过，请查看结果。':'已返回结果，请核对原文。');} catch(error) {$('#answer').innerHTML=empty('本次提问未完成，请查看上方错误提示');throw error;}},body.mode==='llm'?'正在调用真实模型，通常需要几十秒…':'正在检索本项目原文…');});
$('#meeting-example').addEventListener('click',()=>{$('#meeting-text').value='2026-09-08 项目例会（虚构示例）\n已完成：东侧口袋公园基础测绘。\n待办：补充八名居民访谈 | 负责人：陈舟 | 截止：2026-09-15\n待办：完成中心广场照明问题图 | 负责人：许宁 | 截止：2026-09-18\n待办：联系社区确认评审场地 | 负责人：待定 | 截止：待定';});
$('#meeting-form').addEventListener('submit',e=>{e.preventDefault();const body={text:$('#meeting-text').value,mode:$('#meeting-mode').value};work(async()=>{const d=await api(projectPath('/drafts'),{method:'POST',body});await refresh();notice(d.items.length?'已保存草稿，尚未创建任何待办。':'未提取到待办，请检查格式或使用真实模型。');},'正在提取待确认草稿…');});
$('#draft-list').addEventListener('submit',e=>{e.preventDefault();const form=e.target;const d=state.drafts.find(d=>d.id===form.dataset.draft);const items=[...form.querySelectorAll('.draft-item')].filter(el=>el.querySelector('[name=selected]').checked).map(el=>({title:el.querySelector('[name=title]').value,owner:el.querySelector('[name=owner]').value,due_date:el.querySelector('[name=due_date]').value||null,quote:d.items[Number(el.dataset.index)].quote}));const confirmed=form.querySelector('[name=confirmed]').checked;work(async()=>{if(!items.length)throw new Error('请至少勾选一项待办。');if(!confirmed)throw new Error('请先核对并勾选确认。');const result=await api(projectPath(`/drafts/${d.id}/confirm`),{method:'POST',body:{confirmed,items}});await refresh();showPage('tasks');notice(`已创建 ${result.created} 项待办。`);});});
$('#task-list').addEventListener('submit',e=>{e.preventDefault();const form=e.target;const data=Object.fromEntries(new FormData(form));data.due_date=data.due_date||null;work(async()=>{await api(projectPath(`/tasks/${form.dataset.task}`),{method:'PATCH',body:data});await refresh();notice('待办修改已保存。');});});
$('#task-filter').addEventListener('change',renderTasks);
$('#export-runs').addEventListener('click',()=>{const blob=new Blob([JSON.stringify(state.runs,null,2)],{type:'application/json'});const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=`project-${state.project||'none'}-runs.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);notice('运行记录已导出；包含输入原文，分享前请自行检查隐私。');});
$('#close-source').addEventListener('click',()=>$('#source-dialog').close());
$('#access').addEventListener('click',()=>$('#token-dialog').showModal());
$('#close-token').addEventListener('click',()=>$('#token-dialog').close());
async function connect() {const h=await api('/health');$('#model-notice').textContent=h.model.message;await loadProjects(state.project);notice('本机服务已连接。');}
$('#token-form').addEventListener('submit',e=>{e.preventDefault();state.token=$('#token').value;$('#token').value='';$('#token-dialog').close();work(connect,'连接本机服务…');});
work(connect,'连接本机服务…');
