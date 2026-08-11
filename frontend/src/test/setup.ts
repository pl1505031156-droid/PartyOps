import { config } from "@vue/test-utils";
import { vi } from "vitest";
import "fake-indexeddb/auto";

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

class IntersectionObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
  takeRecords() { return []; }
}

Object.defineProperty(window, "matchMedia", {
  configurable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
});

Object.defineProperty(window, "ResizeObserver", { configurable: true, value: ResizeObserverStub });
Object.defineProperty(window, "IntersectionObserver", { configurable: true, value: IntersectionObserverStub });
Object.defineProperty(window, "scrollTo", { configurable: true, value: vi.fn() });
Object.defineProperty(window, "open", { configurable: true, value: vi.fn() });
Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => "blob:test") });
Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
Object.defineProperty(Element.prototype, "scrollIntoView", { configurable: true, value: vi.fn() });

// Arco 组件在页面级测试中只负责容器交互；统一保留插槽，业务按钮、文案与事件仍会渲染。
config.global.renderStubDefaultSlot = true;
