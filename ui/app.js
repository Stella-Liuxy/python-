"use strict";

const $ = (id) => document.getElementById(id);
const token = location.hash.slice(1);
if (token) history.replaceState(null, "", location.pathname);
const view = { file: "", page: 1, next: null, workspace: "", job: "", busy: false, continued: false };
const settings = { base_url: "", model: "", key: "", mcp: true };
const demos = {
  mean: "修复 stats.py 的 mean([])，保留 total 的行为，再运行测试。",
  mcp: "从课堂参考 MCP Server 查询 stdio 协议说明。",
  stop: "启动一个等待命令，检查中断按钮是否能停止它。",
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options, headers: { "X-CursorLab-Token": token, "Content-Type": "application/json" },
  });
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "请求参数有误。");
  return result;
}
function post(path, data) {
  return api(path, { method: "POST", body: data === undefined ? undefined : JSON.stringify(data) });
}
function scrollEvents() {
  $("events").scrollTop = $("events").scrollHeight;
}
function log(text, style) {
  const node = document.createElement("div");
  node.className = "event " + style;
  node.textContent = text;
  $("events").append(node);
  scrollEvents();
  return node;
}
function displayError(error) {
  log(error.message, "error");
}
function setBusy(busy) {
  view.busy = busy;
  $("stop").disabled = !busy;
  $("send").disabled = busy;
  $("pick").disabled = busy;
  $("fresh").disabled = busy;
  document.querySelectorAll("[data-demo]").forEach((button) => { button.disabled = busy; });
}
async function openFile(path, offset = 1) {
  const result = await api("/api/preview?path=" + encodeURIComponent(path) + "&offset=" + offset);
  view.file = path;
  view.page = offset;
  view.next = result.next_offset;
  $("file-tab").textContent = path;
  $("file-content").textContent = result.text || "空文件";
  $("file-meta").textContent = result.total_lines === undefined ? "" :
    result.total_lines + " 行" + (result.remaining ? " · 剩余 " + result.remaining + " 行" : "");
  $("prev").disabled = offset <= 1;
  $("next").disabled = !view.next;
  document.querySelectorAll(".file").forEach((item) => item.setAttribute("aria-current", String(item.dataset.path === path)));
}
async function refreshFiles(prefer = view.file, offset = view.page) {
  const state = await api("/api/files");
  view.workspace = state.workspace;
  $("workspace").textContent = state.workspace.split(/[\\/]/).filter(Boolean).at(-1);
  $("workspace").title = state.workspace;
  $("file-list").replaceChildren();
  for (const path of state.files) {
    const item = document.createElement("button");
    item.className = "file";
    item.dataset.path = path;
    item.textContent = path;
    item.onclick = () => openFile(path).catch(displayError);
    $("file-list").append(item);
  }
  if (state.files.includes(prefer)) await openFile(prefer, offset);
  else if (state.files.length) await openFile(state.files[0]);
  else {
    view.file = "";
    $("file-tab").textContent = "工作区暂无文件";
    $("file-content").textContent = "";
    $("file-meta").textContent = "";
    $("prev").disabled = $("next").disabled = true;
  }
}
function formatTool(result) {
  if (result.error) return result.error;
  if (typeof result.diff === "string") return result.before_lines + " 行 → " + result.after_lines + " 行";
  if ("exit_code" in result) return [
    "exit_code " + result.exit_code + (result.timed_out ? "（超时）" : ""),
    result.stdout && "stdout\n" + result.stdout.trim(),
    result.stderr && "stderr\n" + result.stderr.trim(),
  ].filter(Boolean).join("\n");
  if ("text" in result) return result.text + (result.remaining ? "\n… 还有 " + result.remaining + " 行" : "");
  if (Array.isArray(result.content)) return result.content.map((part) => part.text || part.type).join("\n");
  return result.message || JSON.stringify(result, null, 2);
}
const pending = [];
function showCall(event) {
  const node = document.createElement("details");
  node.className = "tool";
  const title = document.createElement("summary");
  const hint = event.arguments.path || event.arguments.command || event.arguments.topic || "";
  title.textContent = event.name + " · " + hint;
  node.append(title);
  $("events").append(node);
  pending.push({ name: event.name, node, title });
  scrollEvents();
}
function showResult(event) {
  const index = pending.findIndex((item) => item.name === event.name);
  if (index < 0) showCall({ name: event.name, arguments: {} });
  const { node, title } = pending.splice(index < 0 ? pending.length - 1 : index, 1)[0];
  title.textContent += event.ok ? " · 完成" : " · 失败";
  node.classList.toggle("error", !event.ok);
  const output = document.createElement("pre");
  output.textContent = formatTool(event.result);
  if (event.result.diff) {
    output.append("\n");
    for (const line of event.result.diff.trimEnd().split("\n")) {
      const row = document.createElement("span");
      const fileHeader = line.startsWith("+++ ") || line.startsWith("--- ");
      row.className = "diff-line " + (fileHeader ? "" : line.startsWith("+") ? "add" : line.startsWith("-") ? "del" : "");
      row.textContent = line;
      output.append(row);
    }
  }
  node.append(output);
  node.open = !event.ok || event.name === "bash" || event.name.startsWith("mcp__") || Boolean(event.result.diff);
  if (event.ok && ["edit", "write"].includes(event.name)) refreshFiles(event.result.path, 1).catch(displayError);
  scrollEvents();
}
function showApproval(event) {
  const node = log("允许在工作区执行以下命令？\n" + event.command, "approval");
  const actions = document.createElement("div");
  actions.className = "approval-actions";
  for (const [allow, label] of [[false, "拒绝"], [true, "允许本次"]]) {
    const button = document.createElement("button");
    button.textContent = label;
    button.onclick = async () => {
      try {
        await post("/api/tasks/" + view.job + "/approve?allow=" + allow);
        actions.textContent = allow ? "已允许" : "已拒绝";
      } catch (error) { displayError(error); }
    };
    actions.append(button);
  }
  node.append(actions);
  scrollEvents();
}
function onEvent(event) {
  switch (event.type) {
    case "tool_call": showCall(event); break;
    case "tool_result": showResult(event); break;
    case "approval": showApproval(event); break;
    case "done":
      $("status").textContent = { completed: "已完成", cancelled: "已中断", error: "任务失败" }[event.state] || "就绪";
      if (event.message) log(event.message, "status");
      for (const { title } of pending) title.textContent += " · 已结束";
      pending.length = 0;
      document.querySelectorAll(".approval-actions button").forEach((button) => { button.disabled = true; });
      setBusy(false);
      view.continued = event.state === "completed" && !view.demo;
      break;
    default: log(event.message || "", event.ok === false ? "error" : event.type);
  }
}
async function start(prompt, demo = "") {
  if (view.busy || !prompt.trim()) return;
  if (!demo && !settings.key) { $("settings-dialog").showModal(); return; }
  setBusy(true);
  $("status").textContent = "运行中";
  view.demo = demo;
  try {
    const task = await post("/api/tasks", {
      ...(demo ? { mcp: true } : settings), prompt, demo,
      workspace: view.workspace, continued: view.continued,
    });
    view.job = task.id;
    if (demo) $("events").replaceChildren();
    else $("events").querySelector(".welcome")?.remove();
    log(prompt, "user");
    if (demo) log("离线练习 · 固定决策，实际执行工具。", "status");
    refreshFiles(demo ? "stats.py" : view.file, 1).catch(displayError);
    const socket = new WebSocket("ws://" + location.host + "/ws/" + view.job + "?token=" + encodeURIComponent(token));
    socket.onmessage = (message) => onEvent(JSON.parse(message.data));
    socket.onclose = () => {
      if (view.busy && view.job === task.id) {
        post("/api/tasks/" + task.id + "/stop").catch(displayError);
        displayError(new Error("事件连接已断开。"));
        $("status").textContent = "连接断开";
        setBusy(false);
      }
    };
    $("prompt").value = "";
  } catch (error) {
    displayError(error);
    $("status").textContent = "任务失败";
    setBusy(false);
  }
}
$("settings").onclick = () => $("settings-dialog").showModal();
$("close-settings").onclick = () => $("settings-dialog").close();
$("settings-form").onsubmit = (event) => {
  event.preventDefault();
  settings.base_url = $("base-url").value.trim();
  settings.model = $("model-name").value.trim();
  settings.key = $("api-key").value.trim() || settings.key;
  settings.mcp = $("mcp-enabled").checked;
  $("api-key").value = "";
  $("api-key").required = !settings.key;
  $("mode-badge").textContent = settings.model;
  $("settings-dialog").close();
};
$("send").onclick = () => start($("prompt").value);
$("prompt").onkeydown = (event) => {
  if (event.ctrlKey && event.key === "Enter") start($("prompt").value);
};
$("stop").onclick = async () => {
  $("stop").disabled = true;
  $("status").textContent = "正在中断…";
  try { await post("/api/tasks/" + view.job + "/stop"); }
  catch (error) { $("stop").disabled = false; displayError(error); }
};
$("fresh").onclick = () => {
  view.continued = false;
  $("events").replaceChildren();
  log("新会话已就绪。", "status");
};
$("pick").onclick = async () => {
  try {
    if (!window.pywebview?.api) throw new Error("请从桌面应用选择文件夹。");
    const workspace = await window.pywebview.api.pick_workspace();
    if (!workspace) return;
    await post("/api/workspace", { workspace });
    view.continued = false;
    await refreshFiles("", 1);
  } catch (error) { displayError(error); }
};
$("prev").onclick = () => openFile(view.file, Math.max(1, view.page - 120)).catch(displayError);
$("next").onclick = () => openFile(view.file, view.next).catch(displayError);
document.querySelectorAll("[data-demo]").forEach((button) => {
  button.onclick = () => start(demos[button.dataset.demo], button.dataset.demo);
});
refreshFiles("stats.py").catch(displayError);

