<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { Message } from "@arco-design/web-vue";
import { api } from "../api";
import PageHelp from "../components/PageHelp.vue";
import type { PartyDevelopmentMaterial, PartyDevelopmentProfile, PartyDevelopmentRuleMetadata } from "../types";

const profiles = ref<PartyDevelopmentProfile[]>([]);
const rule = ref<PartyDevelopmentRuleMetadata | null>(null);
const loading = ref(false);
const visible = ref(false);
const editing = ref<PartyDevelopmentProfile | null>(null);
const form = reactive({ name: "", description: "", source_label: "本单位补充", active: false, materials_text: "" });
const phases: Record<string, string> = {
  application: "申请入党", activist: "积极分子培养考察", development_object: "发展对象",
  probationary: "预备党员", transition: "转正", archive: "归档",
};

function materialsText(items: PartyDevelopmentMaterial[]) {
  return items.map((item) => [item.phase, item.name, item.responsible_party, item.required ? "必备" : "可选", item.guidance].join("|")).join("\n");
}

function parseMaterials(): PartyDevelopmentMaterial[] {
  return form.materials_text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean).map((line, index) => {
    const [phase = "", name = "", responsible = "", required = "可选", ...guidance] = line.split("|").map((part) => part.trim());
    if (!phases[phase] || !name) throw new Error(`第 ${index + 1} 行的阶段代码或材料名称无效。`);
    return {
      phase, name, responsible_party: responsible, guidance: guidance.join("|").slice(0, 2000),
      required: required === "必备", enabled: true, sort_order: (index + 1) * 10,
    };
  });
}

async function load() {
  loading.value = true;
  try {
    [profiles.value, rule.value] = await Promise.all([
      api.get<PartyDevelopmentProfile[]>("/admin/party-development/profiles"),
      api.get<PartyDevelopmentRuleMetadata>("/party-development/rules/current"),
    ]);
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "补充材料模板加载失败");
  } finally {
    loading.value = false;
  }
}

function openProfile(profile?: PartyDevelopmentProfile) {
  editing.value = profile || null;
  Object.assign(form, {
    name: profile?.name || "", description: profile?.description || "", source_label: profile?.source_label || "本单位补充",
    active: profile?.active ?? false, materials_text: profile ? materialsText(profile.items) : "",
  });
  visible.value = true;
}

async function saveProfile() {
  try {
    const items = parseMaterials();
    if (!form.name.trim()) throw new Error("请填写模板名称。");
    if (editing.value) {
      const updated = await api.patch<PartyDevelopmentProfile>(
        `/admin/party-development/profiles/${editing.value.id}`,
        { name: form.name, description: form.description, source_label: form.source_label, active: form.active },
        { "If-Match": String(editing.value.version) },
      );
      await api.put(
        `/admin/party-development/profiles/${updated.id}/items`, items,
        { "If-Match": String(updated.version) },
      );
    } else {
      await api.post("/admin/party-development/profiles", {
        name: form.name, description: form.description, source_label: form.source_label, active: form.active, items,
      });
    }
    visible.value = false;
    Message.success("补充材料模板已保存；国家规则期限未改变");
    await load();
    return true;
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "保存失败");
    return false;
  }
}

async function toggle(profile: PartyDevelopmentProfile) {
  try {
    await api.patch(`/admin/party-development/profiles/${profile.id}`, { active: !profile.active }, { "If-Match": String(profile.version) });
    Message.success(profile.active ? "模板已停用" : "模板已启用，将只追加材料提示");
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "状态更新失败");
  }
}

async function remove(profile: PartyDevelopmentProfile) {
  try {
    await api.delete(`/admin/party-development/profiles/${profile.id}`, { "If-Match": String(profile.version) });
    Message.success("补充材料模板已删除");
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "删除失败");
  }
}

onMounted(load);
</script>

<template>
  <div class="page settings-page">
    <header class="page-header"><div><p class="page-kicker">管理 · 单位工作口径</p><h1 class="page-title">党员发展补充材料</h1><p class="page-description">只维护本单位材料、责任主体和说明；国家规则节点与期限始终锁定。</p></div><a-space><PageHelp title="党员发展补充材料" :tips="['这里只维护本单位补充材料、责任主体和办理说明，不能改写国家规则期限。', '新模板建议先保持停用，逐项复核后再启用；停用不会删除历史记录。', '删除单位模板前应确认没有后续业务继续依赖，国家规则项不受影响。']" help-query="党员发展 单位模板 国家规则" /><a-button type="primary" @click="openProfile()">新建单位模板</a-button></a-space></header>
    <a-alert type="warning" show-icon class="locked-rule"><template #title>国家规则已锁定 · {{ rule?.title || '2026 年 5 月新版细则' }}</template>管理员不能删除、改写或缩短国家规则。此处配置只会追加到计算结果和 Word 材料清单。</a-alert>
    <section class="profile-grid" :aria-busy="loading">
      <article v-for="profile in profiles" :key="profile.id" :class="{ inactive: !profile.active }">
        <header><div><span>{{ profile.active ? '已启用' : '待确认 / 已停用' }}</span><h2>{{ profile.name }}</h2></div><a-switch :model-value="profile.active" @change="toggle(profile)" /></header>
        <p>{{ profile.description || '尚未填写模板说明。' }}</p>
        <dl><div><dt>来源</dt><dd>{{ profile.source_label }}</dd></div><div><dt>材料</dt><dd>{{ profile.items.length }} 项</dd></div><div><dt>版本</dt><dd>v{{ profile.version }}</dd></div></dl>
        <ul><li v-for="item in profile.items.slice(0, 6)" :key="item.id || `${item.phase}-${item.name}`"><span>{{ phases[item.phase] || item.phase }}</span><b>{{ item.name }}</b><small>{{ item.responsible_party || '责任主体待确认' }}</small></li></ul>
        <footer><a-button size="small" @click="openProfile(profile)">编辑材料</a-button><a-popconfirm content="删除后不可恢复，确认继续？" @ok="remove(profile)"><a-button size="small" status="danger">删除</a-button></a-popconfirm></footer>
      </article>
      <a-empty v-if="!profiles.length && !loading" description="尚未建立补充材料模板" />
    </section>
    <a-modal v-model:visible="visible" :title="editing ? '编辑补充材料模板' : '新建补充材料模板'" width="760px" :mask-closable="false" :on-before-ok="saveProfile">
      <a-alert type="info">首次建议保持停用，待党务管理员逐条核对后再启用。启用也不会参与期限计算。</a-alert>
      <a-form :model="form" layout="vertical" class="profile-form">
        <div class="form-grid"><a-form-item label="模板名称" required><a-input v-model="form.name" :max-length="160" /></a-form-item><a-form-item label="启用"><a-switch v-model="form.active" /></a-form-item></div>
        <a-form-item label="来源说明"><a-input v-model="form.source_label" :max-length="255" /></a-form-item>
        <a-form-item label="用途说明"><a-textarea v-model="form.description" :max-length="2000" :auto-size="{ minRows: 2, maxRows: 4 }" /></a-form-item>
        <a-form-item label="材料条目（每行：阶段代码|材料名称|责任主体|必备/可选|说明）" extra="阶段代码：application、activist、development_object、probationary、transition、archive">
          <a-textarea v-model="form.materials_text" placeholder="activist|季度思想汇报|发展对象本人|可选|按本单位要求确认频次" :auto-size="{ minRows: 8, maxRows: 16 }" />
        </a-form-item>
      </a-form>
    </a-modal>
  </div>
</template>

<style scoped>
.settings-page{max-width:1380px}.locked-rule{margin-bottom:20px}.profile-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(360px,1fr));gap:16px}.profile-grid article{padding:22px;border:1px solid rgba(109,71,47,.18);border-radius:18px;background:rgba(255,252,244,.94);box-shadow:0 12px 30px rgba(76,46,26,.06)}.profile-grid article.inactive{background:rgba(244,240,230,.78)}.profile-grid article>header{display:flex;justify-content:space-between;gap:16px}.profile-grid header span{color:#9b2e27;font-size:11px}.profile-grid h2{margin:4px 0;color:#4a3428;font-family:"Noto Serif SC","Songti SC",serif}.profile-grid p{min-height:42px;color:#806b5b;line-height:1.65}.profile-grid dl{display:grid;grid-template-columns:2fr 1fr 1fr;gap:8px}.profile-grid dl div{padding:9px;border-radius:10px;background:#f7f0e2}.profile-grid dt{color:#9b8978;font-size:10px}.profile-grid dd{margin:3px 0 0;color:#523d30;font-size:12px}.profile-grid ul{display:grid;gap:7px;min-height:92px;padding:14px 0;list-style:none}.profile-grid li{display:grid;grid-template-columns:90px 1fr auto;gap:8px;align-items:center}.profile-grid li span{color:#9c3029;font-size:11px}.profile-grid li small{color:#998777}.profile-grid footer{display:flex;justify-content:flex-end;gap:8px;border-top:1px solid rgba(103,73,49,.11);padding-top:14px}.profile-form{margin-top:16px}.form-grid{display:grid;grid-template-columns:1fr 120px;gap:16px}
</style>
