<template>
  <section class="page" data-module="dosing">
    <header class="page-head">
      <div>
        <h2>加药管理管理</h2>
        <p class="page-desc">维护加药单，围绕加药单号、药剂名称、投加浓度、投加量做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记加药单</button>
        <button class="btn" type="button" @click="exportRows">导出加药管理清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
        <small v-if="item.hint" class="stat-hint">{{ item.hint }}</small>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <label class="filter-item">
        <span>加药状态</span>
        <select v-model="statusFilter">
          <option value="">全部状态</option>
          <option v-for="s in statuses" :key="s" :value="s">{{ s }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <template v-for="action in actions" :key="action">
              <button
                class="link"
                type="button"
                :disabled="!canRun(action, row) || busyId === row.id"
                :title="actionTip(action, row)"
                @click="runAction(action, row)"
              >
                {{ action }}
              </button>
            </template>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">{{ emptyText }}</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条加药管理记录</span>
      <span v-if="errorMessage" class="error-text">
        {{ errorMessage }}
        <button class="link" type="button" @click="reload">重试</button>
      </span>
      <span v-else-if="actionError" class="error-text">
        {{ actionError }}
        <button v-if="retryable" class="link" type="button" @click="retryLastAction">重试本次操作</button>
      </span>
      <span v-else-if="okMessage" class="ok-text">{{ okMessage }}</span>
    </footer>

    <div v-if="showCreate" class="modal-mask" @click.self="closeCreate">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记加药单</h3>
        <p class="modal-desc">加药单号全局唯一，重复登记会被拦下；投加量可留空，填写时必须为不小于 0 的数字。</p>
        <label v-for="field in createFields" :key="field.key" class="form-item">
          <span>{{ field.label }}<em v-if="field.required">*</em></span>
          <input
            v-model="createForm[field.key]"
            :type="field.type || 'text'"
            :placeholder="field.placeholder"
          />
        </label>
        <p v-if="createError" class="error-text form-error">{{ createError }}</p>
        <div class="modal-actions">
          <button class="btn ghost" type="button" :disabled="submitting" @click="closeCreate">取消</button>
          <button class="btn primary" type="submit" :disabled="submitting">
            {{ submitting ? '提交中…' : '确认登记' }}
          </button>
        </div>
      </form>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | boolean | null>

const ENDPOINT = '/api/dosing'
const columns = ['加药单号', '药剂名称', '投加浓度', '投加量', '加药点位', '投加时间', '操作人员', '加药状态']
const actions = ['开始投加', '确认投加', '撤销投加'] as const
const statuses = ['待投加', '投加中', '已投加', '已撤销']
// 状态 → 该状态下还能执行的动作；已投加是事实终态，已撤销需从「开始投加」重新发起。
const ACTIONS_BY_STATUS: Record<string, string[]> = {
  待投加: ['开始投加', '撤销投加'],
  投加中: ['确认投加', '撤销投加'],
  已投加: [],
  已撤销: ['开始投加'],
}

interface CreateField {
  key: string
  label: string
  required: boolean
  type?: string
  placeholder: string
}

const createFields: CreateField[] = [
  { key: '加药单号', label: '加药单号', required: true, placeholder: '如 DOSI-0004' },
  { key: '药剂名称', label: '药剂名称', required: true, placeholder: '如 聚合氯化铝' },
  { key: '投加浓度', label: '投加浓度', required: true, placeholder: '如 5%' },
  { key: '投加量', label: '投加量', required: false, type: 'number', placeholder: '可留空；填写须 ≥ 0' },
  { key: '加药点位', label: '加药点位', required: false, placeholder: '如 1#配药池' },
  { key: '操作人员', label: '操作人员', required: false, placeholder: '当班人员' },
]

const stats = ref([
  { label: '待投加单', value: 0, hint: '状态=待投加' },
  { label: '今日药剂用量', value: 0, hint: '仅统计已投加，已撤销不计入' },
  { label: '撤销单数', value: 0, hint: '状态=已撤销' },
])

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const actionError = ref('')
const okMessage = ref('')
const retryable = ref(false)
const busyId = ref<number | null>(null)
const lastAction = ref<{ action: string; row: Row } | null>(null)
const filters = ref<Record<string, string>>({})
const statusFilter = ref('')
const filterFields = columns.slice(0, 3)

const showCreate = ref(false)
const submitting = ref(false)
const createError = ref('')
const createForm = reactive<Record<string, string>>({})

const hasFilter = computed(() =>
  Boolean(statusFilter.value) || Object.values(filters.value).some((value) => value.trim() !== ''),
)
const emptyText = computed(() =>
  hasFilter.value
    ? '没有符合筛选条件的加药单：请放宽关键字或状态条件后重新查询（不是数据丢失）'
    : '暂无加药单数据：可点击「登记加药单」新建，空列表属于正常初始状态',
)

function canRun(action: string, row: Row): boolean {
  return ACTIONS_BY_STATUS[String(row.status ?? '')]?.includes(action) ?? false
}

function actionTip(action: string, row: Row): string {
  if (canRun(action, row)) return action
  const status = String(row.status ?? '')
  if (status === '已投加') return '已投加为事实终态，不能重复投加或撤销；如需更正请走更正流程'
  if (status === '已撤销') return '已撤销不能直接确认投加，请先「开始投加」重新发起'
  return `当前为「${status}」，需先完成前置状态才能执行${action}`
}

function resetFilters() {
  filters.value = {}
  statusFilter.value = ''
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  for (const field of createFields) createForm[field.key] = ''
  createError.value = ''
  showCreate.value = true
}

function closeCreate() {
  showCreate.value = false
}

async function submitCreate() {
  createError.value = ''
  // 前端先把三个必填项点名报出来，不再只给一句笼统的「缺少必填」。
  const missing = createFields
    .filter((field) => field.required && !createForm[field.key].trim())
    .map((field) => field.label)
  const values: Record<string, string | number> = {}
  for (const field of createFields) {
    const text = createForm[field.key].trim()
    if (text) values[field.key] = field.type === 'number' ? Number(text) : text
  }
  const rawAmount = createForm['投加量'].trim()
  const localErrors: string[] = []
  if (missing.length) localErrors.push(`缺少必填字段：${missing.join('、')}`)
  if (rawAmount !== '') {
    const amount = Number(rawAmount)
    if (!Number.isFinite(amount)) localErrors.push('投加量必须是数字')
    else if (amount < 0) localErrors.push('投加量不能为负数')
  }
  if (localErrors.length) {
    createError.value = localErrors.join('；')
    return
  }

  submitting.value = true // 提交期间锁按钮，中断（网络失败）也会解锁，可原样重发。
  try {
    const response = await request(ENDPOINT, {
      method: 'POST',
      body: JSON.stringify({ values }),
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok || !payload?.ok) {
      // 单号重复、服务端校验失败：表单保留，用户可改完直接再次提交。
      createError.value = payload?.message || `登记失败（HTTP ${response.status}），请修改后重试`
      return
    }
    showCreate.value = false
    okMessage.value = payload.message || '加药单已登记'
    await reload()
  } catch (error) {
    createError.value = `${error instanceof Error ? error.message : '网络中断'}：数据未确认落库，请检查网络后重试（不会产生重复单号，服务端按单号去重）`
  } finally {
    submitting.value = false
  }
}

async function runAction(action: string, row: Row) {
  actionError.value = ''
  okMessage.value = ''
  retryable.value = false
  busyId.value = Number(row.id)
  lastAction.value = { action, row }
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok || !payload?.ok) {
      // 状态不符 / 重复提交属于业务拦截，不是可重试的故障。
      actionError.value = payload?.message || '加药管理动作未生效'
      retryable.value = false
      return
    }
    okMessage.value = payload.message || '操作成功'
    await reload()
  } catch (error) {
    // 请求中断时状态未知：提供重试入口，服务端按状态机幂等拦截重复提交。
    actionError.value = `${error instanceof Error ? error.message : '网络中断'}：动作结果未知，请点击重试或刷新列表确认`
    retryable.value = true
  } finally {
    busyId.value = null
  }
}

async function retryLastAction() {
  if (lastAction.value) await runAction(lastAction.value.action, lastAction.value.row)
}

async function loadSummary() {
  try {
    const response = await request(`${ENDPOINT}/summary`)
    if (!response.ok) return
    const payload = await response.json()
    stats.value[0].value = Number(payload.pending ?? 0)
    stats.value[1].value = Number(payload.today_total ?? 0)
    stats.value[2].value = Number(payload.cancelled ?? 0)
  } catch {
    // 卡片汇总失败不影响列表操作；下次 reload 会再拉一次。
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(filters.value)) {
    if (value.trim()) query.set(key, value.trim())
  }
  if (statusFilter.value) query.set('status', statusFilter.value)
  try {
    const response = await request(`${ENDPOINT}?${query.toString()}`)
    if (!response.ok) throw new Error(`加药单列表读取失败（HTTP ${response.status}）`)
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    // 列表中断时保留旧数据并给出重试入口，而不是清空成「暂无数据」误导用户。
    errorMessage.value = error instanceof Error ? error.message : '加药管理列表读取失败'
  }
  await loadSummary() // 重新进入页面必刷汇总，保证撤销记录与当日合计同口径。
}

onMounted(reload)
</script>

<style scoped>
.stat-hint { display: block; color: var(--muted); font-size: 11px; margin-top: 2px; }
.link:disabled { color: #9aa7b8; cursor: not-allowed; }
.ok-text { color: #067647; }
.modal-mask {
  position: fixed; inset: 0; background: rgba(16, 24, 40, 0.45);
  display: flex; align-items: center; justify-content: center; z-index: 20;
}
.modal {
  background: #fff; border-radius: 10px; padding: 18px 20px; width: 420px;
  display: flex; flex-direction: column; gap: 10px;
}
.modal h3 { margin: 0; }
.modal-desc { margin: 0; font-size: 12px; color: var(--muted); }
.form-item span { display: block; font-size: 12px; color: var(--muted); margin-bottom: 2px; }
.form-item em { color: #b42318; font-style: normal; margin-left: 2px; }
.form-item input { width: 100%; padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; }
.form-error { margin: 0; font-size: 12px; }
.modal-actions { display: flex; justify-content: flex-end; gap: 8px; }
</style>
