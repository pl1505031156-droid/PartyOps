<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { IconBook, IconPhone, IconPlus, IconSearch } from "@arco-design/web-vue/es/icon";
import { Message } from "@arco-design/web-vue";
import { api } from "../api";
import PageHelp from "../components/PageHelp.vue";

interface Knowledge {
  id: string;
  title: string;
  category: string;
  body: string;
  version: number;
  updated_at: string;
}
interface Contact {
  id: string;
  name: string;
  organization: string;
  phone: string;
  note: string;
  version: number;
}

const tab = ref("knowledge");
const entries = ref<Knowledge[]>([]);
const contacts = ref<Contact[]>([]);
const keyword = ref("");
const createVisible = ref(false);
const contactVisible = ref(false);
const editingEntry = ref<Knowledge | null>(null);
const editingContact = ref<Contact | null>(null);
const form = reactive({ title: "", category: "办理经验", body: "" });
const contactForm = reactive({ name: "", organization: "", phone: "", note: "" });

async function load() {
  entries.value = await api.get<Knowledge[]>(`/knowledge${keyword.value ? `?keyword=${encodeURIComponent(keyword.value)}` : ""}`);
  contacts.value = await api.get<Contact[]>("/contacts");
}

async function createEntry() {
  try {
    if (editingEntry.value) {
      await api.patch(
        `/knowledge/${editingEntry.value.id}`,
        form,
        { "If-Match": String(editingEntry.value.version) },
      );
    } else {
      await api.post("/knowledge", form);
    }
    createVisible.value = false;
    form.title = "";
    form.body = "";
    editingEntry.value = null;
    Message.success("知识条目已保存");
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "保存失败");
  }
}

function openEntry(entry?: Knowledge) {
  editingEntry.value = entry || null;
  Object.assign(form, {
    title: entry?.title || "",
    category: entry?.category || "办理经验",
    body: entry?.body || "",
  });
  createVisible.value = true;
}

async function deleteEntry(entry: Knowledge) {
  try {
    await api.delete(`/knowledge/${entry.id}`, { "If-Match": String(entry.version) });
    Message.success("知识条目已删除");
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "删除失败");
  }
}

function openContact(contact?: Contact) {
  editingContact.value = contact || null;
  Object.assign(contactForm, {
    name: contact?.name || "",
    organization: contact?.organization || "",
    phone: contact?.phone || "",
    note: contact?.note || "",
  });
  contactVisible.value = true;
}

async function saveContact() {
  try {
    if (editingContact.value) {
      await api.patch(
        `/contacts/${editingContact.value.id}`,
        contactForm,
        { "If-Match": String(editingContact.value.version) },
      );
    } else {
      await api.post("/contacts", contactForm);
    }
    contactVisible.value = false;
    editingContact.value = null;
    Message.success("联系人已保存");
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "保存失败");
  }
}

async function deleteContact(contact: Contact) {
  try {
    await api.delete(`/contacts/${contact.id}`, { "If-Match": String(contact.version) });
    Message.success("联系人已删除");
    await load();
  } catch (error) {
    Message.error(error instanceof Error ? error.message : "删除失败");
  }
}

onMounted(load);
</script>

<template>
  <div class="page">
    <header class="page-header">
      <div>
        <h1 class="page-title">知识与联系人</h1>
        <p class="page-description">沉淀规范名称、办理流程、易错点、历史反馈和交接说明。</p>
      </div>
      <a-space>
        <PageHelp
          title="知识与联系人怎么用"
          :tips="['把办理规范、易错点和固定联系人沉淀为可复用条目。', '知识条目可与事项和专题双向关联。', '搜索时优先复用已有内容，避免重复整理。']"
          help-query="知识联系人"
        />
        <a-button @click="openContact()"><template #icon><IconPhone /></template>新增联系人</a-button>
        <a-button type="primary" @click="openEntry()"><template #icon><IconPlus /></template>新增知识</a-button>
      </a-space>
    </header>
    <a-tabs v-model:active-key="tab">
      <a-tab-pane key="knowledge" title="知识条目">
        <a-input-search v-model="keyword" class="knowledge-search" placeholder="搜索规范、流程或易错点" @search="load">
          <template #prefix><IconSearch /></template>
        </a-input-search>
        <div v-if="entries.length" class="knowledge-list">
          <article v-for="entry in entries" :key="entry.id">
            <IconBook class="entry-icon" />
            <div>
              <span>{{ entry.category }}</span>
              <h2>{{ entry.title }}</h2>
              <p>{{ entry.body }}</p>
              <div class="entry-actions">
                <button type="button" @click="openEntry(entry)">编辑</button>
                <a-popconfirm content="确定删除这条知识吗？" @ok="deleteEntry(entry)">
                  <button type="button">删除</button>
                </a-popconfirm>
              </div>
            </div>
          </article>
        </div>
        <div v-else class="empty-state">尚无知识条目，可把高频经验沉淀到这里。</div>
      </a-tab-pane>
      <a-tab-pane key="contacts" title="常用联系人">
        <div v-if="contacts.length" class="contact-list">
          <article v-for="contact in contacts" :key="contact.id">
            <div class="contact-avatar"><IconPhone /></div>
            <div><strong>{{ contact.name }}</strong><span>{{ contact.organization }}</span></div>
            <a :href="`tel:${contact.phone}`">{{ contact.phone || "未填写电话" }}</a>
            <p>{{ contact.note }}</p>
            <div class="contact-actions">
              <button type="button" @click="openContact(contact)">编辑</button>
              <a-popconfirm content="确定删除这个联系人吗？" @ok="deleteContact(contact)">
                <button type="button">删除</button>
              </a-popconfirm>
            </div>
          </article>
        </div>
        <div v-else class="empty-state">尚无联系人，可在此添加后供周期事项和交接复用。</div>
      </a-tab-pane>
    </a-tabs>
    <a-modal v-model:visible="createVisible" :title="editingEntry ? '编辑知识条目' : '新增知识条目'" @ok="createEntry">
      <a-form :model="form" layout="vertical">
        <a-form-item label="标题"><a-input v-model="form.title" /></a-form-item>
        <a-form-item label="分类"><a-input v-model="form.category" /></a-form-item>
        <a-form-item label="内容"><a-textarea v-model="form.body" :auto-size="{ minRows: 6, maxRows: 12 }" /></a-form-item>
      </a-form>
    </a-modal>
    <a-modal v-model:visible="contactVisible" :title="editingContact ? '编辑联系人' : '新增联系人'" @ok="saveContact">
      <a-form :model="contactForm" layout="vertical">
        <a-form-item label="姓名"><a-input v-model="contactForm.name" /></a-form-item>
        <a-form-item label="单位或村社区"><a-input v-model="contactForm.organization" /></a-form-item>
        <a-form-item label="联系电话"><a-input v-model="contactForm.phone" /></a-form-item>
        <a-form-item label="办理说明"><a-textarea v-model="contactForm.note" :auto-size="{ minRows: 3, maxRows: 6 }" /></a-form-item>
      </a-form>
    </a-modal>
  </div>
</template>

<style scoped>
.knowledge-search {
  width: 460px;
  margin: 6px 0 20px;
}

.knowledge-list {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1px;
  background: var(--line);
  border: 1px solid var(--line);
}

.knowledge-list article {
  display: flex;
  min-height: 190px;
  gap: 16px;
  padding: 24px;
  background: rgba(251, 248, 241, 0.88);
}

.entry-icon {
  flex: 0 0 auto;
  color: var(--cinnabar);
  font-size: 24px;
}

.knowledge-list span {
  color: var(--cinnabar);
  font-size: 11px;
}

.knowledge-list h2 {
  margin: 8px 0 12px;
  font-size: 18px;
}

.knowledge-list p {
  display: -webkit-box;
  overflow: hidden;
  margin: 0;
  color: var(--muted);
  line-height: 1.75;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 4;
}

.entry-actions,
.contact-actions {
  display: flex;
  gap: 12px;
  margin-top: 12px;
}

.entry-actions button,
.contact-actions button {
  padding: 0;
  color: var(--cinnabar);
  font-size: 11px;
  background: transparent;
  border: 0;
  cursor: pointer;
}

.contact-list {
  border-top: 1px solid var(--line);
}

.contact-list article {
  display: grid;
  min-height: 72px;
  align-items: center;
  grid-template-columns: 44px 220px 160px 1fr 80px;
  gap: 16px;
  border-bottom: 1px solid var(--line-light);
}

.contact-avatar {
  display: grid;
  width: 34px;
  height: 34px;
  color: var(--cinnabar);
  background: #eee3d6;
  border-radius: 50%;
  place-items: center;
}

.contact-list strong,
.contact-list span {
  display: block;
}

.contact-list span,
.contact-list p {
  color: var(--muted);
  font-size: 12px;
}

.contact-actions {
  margin-top: 0;
}
</style>
