<template>
  <main>
    <header class="topbar">
      <span class="brand">光伏组串IV扫描台</span>
      <nav v-if="session">
        <button class="nav" :class="{ active: view === 'scan' }" @click="view = 'scan'">扫描台</button>
        <button class="nav" :class="{ active: view === 'cleaning' }" @click="openCleaning">清洗超期专页</button>
      </nav>
      <span v-if="session" class="who">{{ session.username }}（{{ isWriter ? "扫描员" : "观察员" }}）</span>
    </header>

    <div v-if="!session">
      <p class="sub">扫描员提交开路电压、短路电流与填充因子；通知通道叫醒工人出结论。登录框已预填可写账号 scanner / scan123456。</p>
      <section>
        <label>用户名</label><input v-model="loginUser" autocomplete="off" />
        <label>密码</label><input type="password" v-model="loginPass" autocomplete="off" />
        <button :disabled="loading" @click="login">登录</button>
        <p v-if="error" class="err">{{ error }}</p>
      </section>
    </div>

    <div v-else-if="view === 'scan'">
      <p class="sub">已登录：{{ session.username }}（{{ isWriter ? "可提交" : "只读" }}）</p>
      <div v-if="cleaning && !cleaning.gate_open" class="banner">
        闸口关闭：{{ cleaning.gate_reason }}。提交将被拒收并留痕，详情见清洗超期专页。
      </div>
      <section>
        <button class="secondary" @click="logout">退出</button>
        <button class="secondary" @click="tick">刷新列表</button>
      </section>
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
    </div>

    <div v-else>
      <p class="sub">清洗周期超期时可在此一刀切暂停整场扫描；阈值区、暂停记录流水区与说明区并排如下。</p>
      <div v-if="cleaning" class="cols">
        <section class="col">
          <h2>阈值区</h2>
          <p>清洗周期阈值：<b>{{ cleaning.threshold_days === null ? "（空）" : cleaning.threshold_days + " 天" }}</b></p>
          <p>上次清洗：{{ fmt(cleaning.last_cleaned_at) }}</p>
          <p>超期状态：
            <span class="tag" :class="cleaning.overdue ? 'bad' : 'ok'">{{ cleaning.overdue ? "已超期" : "正常" }}</span>
          </p>
          <p>闸口：
            <span class="tag" :class="cleaning.gate_open ? 'ok' : 'bad'">{{ cleaning.gate_open ? "开启" : "关闭" }}</span>
            <span class="muted">{{ cleaning.gate_reason }}</span>
          </p>
          <template v-if="isWriter">
            <label>设定阈值（天），留空即清空</label>
            <input type="number" step="0.01" min="0" v-model="thresholdInput" placeholder="例如 30；压得很低可立刻造出超期" />
            <button :disabled="loading" @click="saveThreshold">保存阈值</button>
            <button class="secondary" :disabled="loading" @click="clearThreshold">清空阈值</button>
            <hr />
            <button v-if="!cleaning.paused" class="danger" :disabled="loading" @click="pauseAll">一刀切暂停整场扫描</button>
            <button v-else :disabled="loading" @click="resumeAll">开闸恢复扫描</button>
          </template>
          <p v-else class="muted">观察员能看阈值与暂停记录，不能扳暂停钮。</p>
          <p v-if="cleanErr" class="err">{{ cleanErr }}</p>
        </section>

        <section class="col">
          <h2>暂停记录流水区</h2>
          <table>
            <thead>
              <tr><th>时间</th><th>类型</th><th>操作人</th><th>说明</th></tr>
            </thead>
            <tbody>
              <tr v-for="ev in cleaning.events" :key="ev.id">
                <td>{{ fmt(ev.created_at) }}</td>
                <td><span class="tag" :class="kindClass(ev.kind)">{{ kindLabel(ev.kind) }}</span></td>
                <td>{{ ev.actor }}</td>
                <td>{{ ev.reason }}<template v-if="ev.string_code">（组串：{{ ev.string_code }}）</template></td>
              </tr>
              <tr v-if="!cleaning.events.length"><td colspan="4" class="muted">暂无记录</td></tr>
            </tbody>
          </table>
        </section>

        <section class="col">
          <h2>说明区</h2>
          <ul class="notes">
            <li>清洗周期超期时可一刀切暂停整场扫描：写入通道拒收新单，工人同时停止认领。</li>
            <li>超期检测在保存阈值时触发：一旦越过「上次清洗 + 阈值」即自动落闸并记入流水。</li>
            <li>闸口关闭时交单会被拒收，拦住痕迹与真实拒收同批落库，见中间流水区。</li>
            <li>阈值空着不许开闸：未设阈值时闸口保持关闭，也无法手动开闸。</li>
            <li>观察员能看阈值与暂停记录，不能扳暂停钮。</li>
          </ul>
        </section>
      </div>
    </div>
  </main>
</template>
<script setup>
import { computed, onMounted, onUnmounted, ref } from "vue";
const session = ref(null);
const logs = ref([]);
const cleaning = ref(null);
const view = ref("scan");
const loginUser = ref("scanner");
const loginPass = ref("scan123456");
const stringCode = ref("");
const voc = ref("");
const isc = ref("");
const ff = ref("");
const thresholdInput = ref("");
const error = ref("");
const cleanErr = ref("");
const loading = ref(false);
let timer;
const isWriter = computed(() => session.value?.role === "writer");
function headers() {
  return session.value ? { Authorization: "Bearer " + session.value.token } : {};
}
function fmt(iso) {
  return iso ? new Date(iso).toLocaleString() : "—";
}
function kindLabel(kind) {
  return { pause: "暂停", resume: "开闸", reject: "拒收" }[kind] || kind;
}
function kindClass(kind) {
  return { pause: "bad", resume: "ok", reject: "pending" }[kind] || "pending";
}
async function refresh() {
  if (!session.value) return;
  const res = await fetch("/api/logs", { headers: headers() });
  if (res.status === 401) { logout(); return; }
  if (res.ok) logs.value = await res.json();
}
async function loadCleaning() {
  if (!session.value) return;
  const res = await fetch("/api/cleaning", { headers: headers() });
  if (res.status === 401) { logout(); return; }
  if (res.ok) cleaning.value = await res.json();
}
function tick() {
  refresh();
  loadCleaning();
}
function openCleaning() {
  view.value = "cleaning";
  cleanErr.value = "";
  loadCleaning();
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
    await tick();
    timer = setInterval(tick, 2000);
  } catch { error.value = "无法连接接口"; }
  finally { loading.value = false; }
}
function logout() {
  if (timer) clearInterval(timer);
  session.value = null;
  logs.value = [];
  cleaning.value = null;
  view.value = "scan";
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
    if (!res.ok) { error.value = data.detail || "提交失败"; tick(); return; }
    stringCode.value = voc.value = isc.value = ff.value = "";
    await tick();
  } catch { error.value = "提交时网络异常"; }
  finally { loading.value = false; }
}
async function saveThreshold() {
  cleanErr.value = "";
  loading.value = true;
  try {
    const val = thresholdInput.value === "" ? null : Number(thresholdInput.value);
    const res = await fetch("/api/cleaning/threshold", {
      method: "PUT",
      headers: { "Content-Type": "application/json", ...headers() },
      body: JSON.stringify({ threshold_days: val }),
    });
    const data = await res.json();
    if (!res.ok) { cleanErr.value = data.detail || "保存阈值失败"; return; }
    cleaning.value = data;
    thresholdInput.value = "";
  } catch { cleanErr.value = "保存阈值时网络异常"; }
  finally { loading.value = false; }
}
async function clearThreshold() {
  thresholdInput.value = "";
  await saveThreshold();
}
async function pauseAll() {
  cleanErr.value = "";
  loading.value = true;
  try {
    const res = await fetch("/api/cleaning/pause", { method: "POST", headers: headers() });
    const data = await res.json();
    if (!res.ok) { cleanErr.value = data.detail || "暂停失败"; return; }
    cleaning.value = data;
  } catch { cleanErr.value = "暂停时网络异常"; }
  finally { loading.value = false; }
}
async function resumeAll() {
  cleanErr.value = "";
  loading.value = true;
  try {
    const res = await fetch("/api/cleaning/resume", { method: "POST", headers: headers() });
    const data = await res.json();
    if (!res.ok) { cleanErr.value = data.detail || "开闸失败"; return; }
    cleaning.value = data;
  } catch { cleanErr.value = "开闸时网络异常"; }
  finally { loading.value = false; }
}
onMounted(() => {
  const raw = localStorage.getItem("pv_session");
  if (raw) {
    try {
      session.value = JSON.parse(raw);
      tick();
      timer = setInterval(tick, 2000);
    } catch { localStorage.removeItem("pv_session"); }
  }
});
onUnmounted(() => { if (timer) clearInterval(timer); });
</script>
<style>
body { margin: 0; font-family: "Segoe UI", system-ui, sans-serif; background: #052e16; color: #ecfdf5; }
main { max-width: 1180px; margin: 0 auto; padding: 1.5rem; }
.topbar { display: flex; align-items: center; gap: 1rem; border-bottom: 1px solid #166534; padding-bottom: 0.75rem; margin-bottom: 1rem; }
.brand { color: #86efac; font-size: 1.3rem; font-weight: 700; }
.topbar nav { display: flex; gap: 0.4rem; flex: 1; }
button.nav { background: #14532d; color: #a7f3d0; border: 1px solid #166534; }
button.nav.active { background: #16a34a; color: #fff; }
.who { color: #a7f3d0; font-size: 0.85rem; }
.sub { color: #a7f3d0; margin-bottom: 1.25rem; }
section { background: #14532d; border: 1px solid #166534; border-radius: 8px; padding: 1rem 1.25rem; margin-bottom: 1rem; }
h2 { font-size: 1rem; color: #86efac; margin: 0 0 0.75rem; }
label { display: block; font-size: 0.85rem; margin-bottom: 0.25rem; }
input { width: 100%; box-sizing: border-box; padding: 0.5rem 0.65rem; border-radius: 6px; border: 1px solid #4ade80; background: #022c22; color: #ecfdf5; margin-bottom: 0.75rem; }
button { cursor: pointer; padding: 0.5rem 1rem; border: none; border-radius: 6px; background: #16a34a; color: #fff; font-weight: 600; margin-right: 0.4rem; }
button.secondary { background: #365314; }
button.danger { background: #b91c1c; }
button:disabled { opacity: 0.6; cursor: not-allowed; }
.err { color: #fecaca; }
.muted { color: #86a98f; font-size: 0.85rem; }
.banner { background: #7f1d1d; border: 1px solid #b91c1c; border-radius: 8px; padding: 0.75rem 1rem; margin-bottom: 1rem; color: #fecaca; }
.cols { display: flex; gap: 1rem; align-items: flex-start; }
.cols .col { flex: 1; min-width: 0; }
hr { border: none; border-top: 1px solid #166534; margin: 1rem 0; }
.notes { margin: 0; padding-left: 1.1rem; color: #a7f3d0; font-size: 0.88rem; line-height: 1.7; }
table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
th, td { text-align: left; padding: 0.45rem; border-bottom: 1px solid #166534; }
.tag { padding: 0.1rem 0.4rem; border-radius: 4px; font-size: 0.8rem; }
.ok { background: #14532d; color: #bbf7d0; }
.bad { background: #7f1d1d; color: #fecaca; }
.pending { background: #854d0e; color: #fde68a; }
</style>
