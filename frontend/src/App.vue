<template>
  <main>
    <h1>光伏组串IV扫描台</h1>
    <div v-if="!session">
      <p class="sub">扫描员提交开路电压、短路电流与填充因子;通知通道叫醒工人出结论。登录框已预填可写账号 scanner / scan123456。</p>
      <section>
        <label>用户名</label><input v-model="loginUser" autocomplete="off" />
        <label>密码</label><input type="password" v-model="loginPass" autocomplete="off" />
        <button :disabled="loading" @click="login">登录</button>
        <p v-if="error" class="err">{{ error }}</p>
      </section>
    </div>
    <div v-else>
      <header class="topbar">
        <span class="brand">已登录:{{ session.username }}({{ isWriter ? "可提交" : "只读" }})</span>
        <span v-if="cleaning" class="chip" :class="cleaning.gate_open ? 'ok' : 'bad'">
          闸口:{{ gateText }}
        </span>
        <span class="spacer"></span>
        <button class="secondary" @click="view = view === 'cleaning' ? 'scan' : 'cleaning'">
          {{ view === "cleaning" ? "返回扫描台" : "清洗超期专页" }}
        </button>
        <button class="secondary" @click="refreshAll">刷新列表</button>
        <button class="secondary" @click="logout">退出</button>
      </header>

      <template v-if="view === 'scan'">
        <p v-if="cleaning && !cleaning.gate_open" class="banner">
          扫描闸口已关闭({{ gateText }}),交单将被写入通道拦回并记入暂停记录流水。
        </p>
        <section v-if="isWriter">
          <label>组串编号</label><input v-model="stringCode" placeholder="例如 阵列C-串05" />
          <label>开路电压 V</label><input type="number" step="0.1" v-model="voc" />
          <label>短路电流 A</label><input type="number" step="0.1" v-model="isc" />
          <label>填充因子</label><input type="number" step="0.01" v-model="ff" />
          <button :disabled="loading" @click="submit">提交扫描</button>
          <p v-if="error" class="err">{{ error }}</p>
        </section>
        <section>
          <table>
            <thead>
              <tr><th>编号</th><th>组串</th><th>Voc</th><th>Isc</th><th>FF</th><th>状态</th><th>结论</th></tr>
            </thead>
            <tbody>
              <tr v-for="row in logs" :key="row.id">
                <td>{{ row.id }}</td>
                <td>{{ row.string_code }}</td>
                <td>{{ row.voc_v }}</td>
                <td>{{ row.isc_a }}</td>
                <td>{{ row.fill_factor }}</td>
                <td><span class="tag" :class="row.status === 'pending' ? 'pending' : 'ok'">{{ row.status === 'pending' ? '待处理' : '已完成' }}</span></td>
                <td><span v-if="row.verdict" class="tag" :class="row.verdict === '合格' ? 'ok' : 'bad'">{{ row.verdict }}</span><span v-else>—</span></td>
              </tr>
            </tbody>
          </table>
        </section>
      </template>

      <template v-else>
        <div class="cols">
          <section>
            <h2>阈值区</h2>
            <template v-if="cleaning">
              <p>清洗上限:<b>{{ cleaning.threshold_hours === null ? "未设置(空着不许开闸)" : cleaning.threshold_hours + " 小时" }}</b></p>
              <p>周期起点:{{ cleaning.cycle_started_at ? fmtTime(cleaning.cycle_started_at) : "—" }}</p>
              <p>已运行:{{ cleaning.elapsed_hours === null ? "—" : cleaning.elapsed_hours + " 小时" }}</p>
              <p>超期检测:<span class="tag" :class="cleaning.overdue ? 'bad' : 'ok'">{{ cleaning.overdue ? "已超期" : "未超期" }}</span></p>
              <p>暂停闸:
                <span class="tag" :class="cleaning.paused ? 'bad' : 'ok'">
                  {{ cleaning.paused ? (cleaning.pause_source === "auto" ? "已暂停(超期自动)" : "已暂停(手动)") : "未暂停" }}
                </span>
              </p>
              <p v-if="cleaning.overdue_override" class="muted">本次超期已被手动恢复豁免,阈值变更前不再自动扳闸。</p>
            </template>
            <template v-if="isWriter">
              <label>设置清洗上限(小时)</label>
              <input type="number" step="any" min="0" v-model="thresholdInput" placeholder="例如 720,或 0.0001 制造超期" />
              <button :disabled="loading" @click="saveThreshold">保存阈值</button>
              <button class="secondary" :disabled="loading" @click="clearThreshold">清空阈值</button>
              <hr />
              <button v-if="cleaning && !cleaning.paused" class="danger" :disabled="loading" @click="togglePause('pause')">一刀切暂停整场扫描</button>
              <button v-else :disabled="loading" @click="togglePause('resume')">关掉暂停,恢复扫描</button>
            </template>
            <p v-else class="muted">观察员只读:能看阈值与暂停记录,不能扳暂停钮。</p>
            <p v-if="cleaningError" class="err">{{ cleaningError }}</p>
          </section>

          <section>
            <h2>暂停记录流水区</h2>
            <ul class="events">
              <li v-for="ev in events" :key="ev.id">
                <span class="tag" :class="evtClass(ev.event_type)">{{ evtLabel(ev.event_type) }}</span>
                <div class="evbody">
                  <div>{{ ev.detail }}</div>
                  <div class="muted">{{ fmtTime(ev.created_at) }} · {{ ev.actor }}<template v-if="ev.string_code"> · 组串 {{ ev.string_code }}</template></div>
                </div>
              </li>
              <li v-if="!events.length" class="muted">暂无记录</li>
            </ul>
          </section>

          <section>
            <h2>说明区</h2>
            <ul class="notes">
              <li>清洗周期超期时,可一刀切暂停整场扫描:超期检测自动扳闸,扫描员也可手动扳闸。</li>
              <li>超期检测接到退回、再接到写入通道:闸口关闭时交单被接口拒收(409),不是只改页面开关。</li>
              <li>拦住痕迹与真实拒收同批落库:每一次拦回都在拒收的同一事务里写入上方流水。</li>
              <li>阈值空着不许开闸:未设清洗上限时闸口保持关闭,交单一律拦回。</li>
              <li>观察员能看阈值与暂停记录,不能扳暂停钮;阈值设置与暂停开关仅扫描员可动。</li>
              <li>调阈值不重置周期起点,把上限压低会立即超期;清空再设阈值等于开启新清洗周期。</li>
              <li>超期中手动恢复视为豁免本次超期,阈值变更后恢复自动检测。</li>
              <li>暂停只拦新交单,已入队的存量仍由工人处理完。</li>
            </ul>
          </section>
        </div>
      </template>
    </div>
  </main>
</template>
<script setup>
import { computed, onMounted, onUnmounted, ref } from "vue";
const session = ref(null);
const logs = ref([]);
const view = ref("scan");
const cleaning = ref(null);
const events = ref([]);
const thresholdInput = ref("");
const cleaningError = ref("");
const loginUser = ref("scanner");
const loginPass = ref("scan123456");
const stringCode = ref("");
const voc = ref("");
const isc = ref("");
const ff = ref("");
const error = ref("");
const loading = ref(false);
let timer;
const isWriter = computed(() => session.value?.role === "writer");
const gateText = computed(() => {
  const c = cleaning.value;
  if (!c) return "未知";
  if (c.gate_open) return "开闸";
  if (c.threshold_hours === null) return "闭(未设阈值)";
  return "闭(已暂停)";
});
function headers() {
  return session.value ? { Authorization: "Bearer " + session.value.token } : {};
}
function fmtTime(iso) {
  return new Date(iso).toLocaleString();
}
function evtLabel(t) {
  return { auto_pause: "自动暂停", manual_pause: "手动暂停", manual_resume: "恢复", threshold_set: "阈值变更", intercept: "拦截" }[t] || t;
}
function evtClass(t) {
  return t === "intercept" ? "pending" : t === "manual_resume" || t === "threshold_set" ? "ok" : "bad";
}
async function refresh() {
  if (!session.value) return;
  const res = await fetch("/api/logs", { headers: headers() });
  if (res.status === 401) { logout(); return; }
  if (res.ok) logs.value = await res.json();
}
async function fetchCleaning() {
  if (!session.value) return;
  const [s, e] = await Promise.all([
    fetch("/api/cleaning/status", { headers: headers() }),
    fetch("/api/cleaning/events", { headers: headers() }),
  ]);
  if (s.status === 401) { logout(); return; }
  if (s.ok) cleaning.value = await s.json();
  if (e.ok) events.value = await e.json();
}
function refreshAll() {
  refresh();
  fetchCleaning();
}
async function login() {
  error.value = "";
  loading.value = true;
  try {
    const res = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: loginUser.value, password: loginPass.value }),
    });
    const data = await res.json();
    if (!res.ok) { error.value = data.detail || "登录失败"; return; }
    session.value = { token: data.access_token, username: data.username, role: data.role };
    localStorage.setItem("pv_session", JSON.stringify(session.value));
    await refreshAll();
    timer = setInterval(refreshAll, 2000);
  } catch { error.value = "无法连接接口"; }
  finally { loading.value = false; }
}
function logout() {
  if (timer) clearInterval(timer);
  session.value = null;
  logs.value = [];
  cleaning.value = null;
  events.value = [];
  localStorage.removeItem("pv_session");
}
async function submit() {
  error.value = "";
  loading.value = true;
  try {
    const res = await fetch("/api/logs", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...headers() },
      body: JSON.stringify({
        string_code: stringCode.value,
        voc_v: Number(voc.value),
        isc_a: Number(isc.value),
        fill_factor: Number(ff.value),
      }),
    });
    const data = await res.json();
    if (!res.ok) { error.value = data.detail || "提交失败"; fetchCleaning(); return; }
    stringCode.value = voc.value = isc.value = ff.value = "";
    await refreshAll();
  } catch { error.value = "提交时网络异常"; }
  finally { loading.value = false; }
}
async function saveThreshold() {
  cleaningError.value = "";
  if (thresholdInput.value === "" || isNaN(Number(thresholdInput.value))) {
    cleaningError.value = "请输入数字阈值(小时),或点清空阈值";
    return;
  }
  await sendThreshold(Number(thresholdInput.value));
}
async function clearThreshold() {
  cleaningError.value = "";
  await sendThreshold(null);
}
async function sendThreshold(value) {
  loading.value = true;
  try {
    const res = await fetch("/api/cleaning/threshold", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...headers() },
      body: JSON.stringify({ threshold_hours: value }),
    });
    const data = await res.json();
    if (!res.ok) { cleaningError.value = data.detail || "保存失败"; return; }
    cleaning.value = data;
    thresholdInput.value = "";
    await fetchCleaning();
  } catch { cleaningError.value = "网络异常"; }
  finally { loading.value = false; }
}
async function togglePause(action) {
  cleaningError.value = "";
  loading.value = true;
  try {
    const res = await fetch("/api/cleaning/toggle", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...headers() },
      body: JSON.stringify({ action }),
    });
    const data = await res.json();
    if (!res.ok) { cleaningError.value = data.detail || "操作失败"; return; }
    cleaning.value = data;
    await fetchCleaning();
  } catch { cleaningError.value = "网络异常"; }
  finally { loading.value = false; }
}
onMounted(() => {
  const raw = localStorage.getItem("pv_session");
  if (raw) {
    try {
      session.value = JSON.parse(raw);
      refreshAll();
      timer = setInterval(refreshAll, 2000);
    } catch { localStorage.removeItem("pv_session"); }
  }
});
onUnmounted(() => { if (timer) clearInterval(timer); });
</script>
<style>
body { margin: 0; font-family: "Segoe UI", system-ui, sans-serif; background: #052e16; color: #ecfdf5; }
main { max-width: 1180px; margin: 0 auto; padding: 1.5rem; }
h1 { color: #86efac; margin: 0 0 0.25rem; }
h2 { color: #86efac; font-size: 1rem; margin: 0 0 0.75rem; }
.sub { color: #a7f3d0; margin-bottom: 1.25rem; }
section { background: #14532d; border: 1px solid #166534; border-radius: 8px; padding: 1rem 1.25rem; margin-bottom: 1rem; }
label { display: block; font-size: 0.85rem; margin-bottom: 0.25rem; }
input { width: 100%; box-sizing: border-box; padding: 0.5rem 0.65rem; border-radius: 6px; border: 1px solid #4ade80; background: #022c22; color: #ecfdf5; margin-bottom: 0.75rem; }
button { cursor: pointer; padding: 0.5rem 1rem; border: none; border-radius: 6px; background: #16a34a; color: #fff; font-weight: 600; margin-right: 0.4rem; }
button.secondary { background: #365314; }
button.danger { background: #b91c1c; }
button:disabled { opacity: 0.6; cursor: not-allowed; }
.err { color: #fecaca; }
.muted { color: #a7f3d0; opacity: 0.75; font-size: 0.85rem; }
table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
th, td { text-align: left; padding: 0.45rem; border-bottom: 1px solid #166534; }
.tag { padding: 0.1rem 0.4rem; border-radius: 4px; font-size: 0.8rem; }
.ok { background: #14532d; color: #bbf7d0; }
.bad { background: #7f1d1d; color: #fecaca; }
.pending { background: #854d0e; color: #fde68a; }
.topbar { display: flex; align-items: center; gap: 0.6rem; background: #14532d; border: 1px solid #166534; border-radius: 8px; padding: 0.6rem 1rem; margin-bottom: 1rem; }
.topbar .brand { font-weight: 600; color: #a7f3d0; }
.topbar .spacer { flex: 1; }
.chip { padding: 0.15rem 0.5rem; border-radius: 4px; font-size: 0.85rem; }
.banner { background: #7f1d1d; color: #fecaca; border-radius: 6px; padding: 0.6rem 1rem; }
.cols { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 1rem; align-items: start; }
@media (max-width: 960px) { .cols { grid-template-columns: 1fr; } }
.events { list-style: none; margin: 0; padding: 0; max-height: 480px; overflow-y: auto; }
.events li { display: flex; gap: 0.5rem; padding: 0.45rem 0; border-bottom: 1px solid #166534; align-items: flex-start; }
.events .tag { white-space: nowrap; margin-top: 0.1rem; }
.evbody { flex: 1; font-size: 0.9rem; }
.notes { margin: 0; padding-left: 1.1rem; font-size: 0.9rem; line-height: 1.6; }
hr { border: none; border-top: 1px solid #166534; margin: 0.75rem 0; }
</style>
