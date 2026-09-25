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
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>加药单号</span>
        <input v-model="filters.keyword" placeholder="按加药单号检索" />
      </label>
      <label class="filter-item">
        <span>药剂名称</span>
        <input v-model="filters.chemical" placeholder="按药剂名称检索" />
      </label>
      <label class="filter-item">
        <span>投加浓度</span>
        <input v-model="filters.concentration" placeholder="按投加浓度检索" />
      </label>
      <label class="filter-item">
        <span>加药状态</span>
        <select v-model="filters.status">
          <option value="">全部状态</option>
          <option v-for="status in statuses" :key="status" :value="status">{{ status }}</option>
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
            <template v-if="actionsFor(row).length">
              <button
                v-for="action in actionsFor(row)"
                :key="action"
                class="link"
                type="button"
                :disabled="acting"
                @click="runAction(action, row)"
              >
                {{ action }}
              </button>
            </template>
            <span v-else class="terminal-hint">{{ terminalHint(row) }}</span>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无加药管理数据，可先登记加药单</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条加药管理记录</span>
      <span v-if="errorMessage" class="error-text">
        {{ errorMessage }}
        <button v-if="failedAction" class="link" type="button" @click="retryFailed">重试</button>
      </span>
    </footer>

    <div v-if="showCreate" class="dialog-mask" @click.self="closeCreate">
      <form class="dialog" @submit.prevent="submitCreate">
        <h3>登记加药单</h3>
        <label v-for="field in createFields" :key="field" class="dialog-item">
          <span>{{ field }}<em v-if="requiredFields.includes(field)" class="required">*</em></span>
          <input v-model="createForm[field]" :placeholder="`请输入${field}`" />
        </label>
        <p v-if="createMessage" class="error-text">{{ createMessage }}</p>
        <div class="dialog-actions">
          <button class="btn primary" type="submit" :disabled="submitting">
            {{ submitting ? '提交中…' : '确认登记' }}
          </button>
          <button class="btn ghost" type="button" @click="closeCreate">取消</button>
        </div>
      </form>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/dosing'
const columns = ["加药单号", "药剂名称", "投加浓度", "投加量", "加药点位", "投加时间", "操作人员", "加药状态"]
const statuses = ["待投加", "投加中", "已投加", "已撤销"]
// 与后端 TRANSITIONS 保持一致：已投加、已撤销是终态，不再给任何动作。
const ACTIONS_BY_STATUS: Record<string, string[]> = {
  '待投加': ['开始投加', '确认投加', '撤销投加'],
  '投加中': ['确认投加', '撤销投加'],
  '已投加': [],
  '已撤销': [],
}
const createFields = ["加药单号", "药剂名称", "投加浓度", "投加量", "加药点位", "投加时间", "操作人员"]
const requiredFields = ["加药单号", "药剂名称", "投加浓度"]

const rows = ref<Row[]>([])
const total = ref(0)
const stats = ref([
  { label: '待投加单', value: 0 },
  { label: '今日药剂用量', value: 0 },
  { label: '撤销单数', value: 0 },
])
const errorMessage = ref('')
const filters = ref<Record<string, string>>({ keyword: '', chemical: '', concentration: '', status: '' })
const acting = ref(false)
const failedAction = ref<{ action: string; row: Row } | null>(null)
const showCreate = ref(false)
const submitting = ref(false)
const createMessage = ref('')
const createForm = ref<Record<string, string>>({})

function actionsFor(row: Row): string[] {
  return ACTIONS_BY_STATUS[String(row.status ?? '')] ?? []
}

function terminalHint(row: Row): string {
  const status = String(row.status ?? '')
  if (status === '已撤销') return '已撤销，不可再投加'
  if (status === '已投加') return '已投加，已计入当日用量'
  return '—'
}

function resetFilters() {
  filters.value = { keyword: '', chemical: '', concentration: '', status: '' }
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  createForm.value = Object.fromEntries(createFields.map((field) => [field, '']))
  createMessage.value = ''
  showCreate.value = true
}

function closeCreate() {
  if (submitting.value) return
  showCreate.value = false
}

async function readPayload(response: Response): Promise<{ ok?: boolean; message?: string; detail?: string }> {
  try {
    return (await response.json()) as { ok?: boolean; message?: string; detail?: string }
  } catch {
    return {}
  }
}

async function submitCreate() {
  if (submitting.value) return // 防止连点重复登记
  submitting.value = true
  createMessage.value = ''
  try {
    const response = await request(ENDPOINT, {
      method: 'POST',
      body: JSON.stringify({ values: createForm.value }),
    })
    const payload = await readPayload(response)
    if (!response.ok || payload.ok === false) {
      throw new Error(payload.message ?? payload.detail ?? '加药单登记失败，请核对后重试')
    }
    showCreate.value = false
    await Promise.all([reload(), loadSummary()])
  } catch (error) {
    createMessage.value = error instanceof Error ? error.message : '加药单登记失败，请核对后重试'
  } finally {
    submitting.value = false
  }
}

async function runAction(action: string, row: Row) {
  if (acting.value) return // 上一个动作未结束时不再重复发请求
  acting.value = true
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const payload = await readPayload(response)
    if (!response.ok || payload.ok === false) {
      throw new Error(payload.message ?? payload.detail ?? '加药管理动作未生效')
    }
    failedAction.value = null
    await Promise.all([reload(), loadSummary()])
  } catch (error) {
    failedAction.value = { action, row }
    const detail = error instanceof Error ? error.message : '加药管理操作失败'
    errorMessage.value = `${action}未生效：${detail}`
  } finally {
    acting.value = false
  }
}

async function retryFailed() {
  const pending = failedAction.value
  if (pending) await runAction(pending.action, pending.row)
}

async function reload() {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(filters.value)) {
    if (value) query.set(key, value)
  }
  try {
    const response = await request(`${ENDPOINT}?${query.toString()}`)
    if (!response.ok) {
      throw new Error('加药单列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '加药管理列表读取失败'
  }
}

async function loadSummary() {
  try {
    const response = await request(`${ENDPOINT}/summary`)
    if (!response.ok) {
      throw new Error('加药统计读取失败')
    }
    const payload = await response.json()
    stats.value = [
      { label: '待投加单', value: payload.pending ?? 0 },
      { label: '今日药剂用量', value: payload.today_amount ?? 0 },
      { label: '撤销单数', value: payload.canceled ?? 0 },
    ]
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '加药统计读取失败'
  }
}

onMounted(() => {
  void reload()
  void loadSummary()
})
</script>

<style scoped>
.terminal-hint { color: var(--muted); font-size: 12px; }
.dialog-mask { position: fixed; inset: 0; background: rgba(15, 23, 42, 0.35); display: flex; align-items: center; justify-content: center; }
.dialog { background: #fff; border-radius: 8px; padding: 16px 20px; width: 360px; display: flex; flex-direction: column; gap: 10px; }
.dialog h3 { margin: 0; font-size: 15px; }
.dialog-item span { display: block; font-size: 12px; color: var(--muted); }
.dialog-item input { width: 100%; padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; }
.required { color: #b42318; font-style: normal; margin-left: 2px; }
.dialog-actions { display: flex; gap: 8px; justify-content: flex-end; }
</style>
