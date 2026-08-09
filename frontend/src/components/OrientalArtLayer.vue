<script setup lang="ts">
import { computed, onBeforeUnmount, watchEffect } from "vue";
import {
  solarTermToken,
  type ArtSlot,
  type OrientalSceneConfig,
} from "../theme/oriental";

const props = withDefaults(defineProps<{
  config: OrientalSceneConfig;
  /** 交节当天才显示的节气名称。 */
  solarTerm?: string;
  /** 在下一节气到来前持续驱动画景微变量。 */
  activeSolarTerm?: string;
  standalone?: boolean;
}>(), {
  solarTerm: "",
  activeSolarTerm: "",
  standalone: false,
});

const layerClass = computed(() => [
  `art-profile-${props.config.profile}`,
  `art-scene-${props.config.scene}`,
  `art-motif-${props.config.motif}`,
  `art-composition-${props.config.composition}`,
  { "art-standalone": props.standalone },
]);

function hasSlot(slot: ArtSlot): boolean {
  return props.config.slots.includes(slot);
}

watchEffect(() => {
  if (typeof document === "undefined") return;
  document.documentElement.dataset.artProfile = props.config.profile;
  document.documentElement.dataset.scene = props.config.scene;
  document.documentElement.dataset.solarTerm = solarTermToken(props.activeSolarTerm);
});

onBeforeUnmount(() => {
  if (typeof document === "undefined") return;
  delete document.documentElement.dataset.artProfile;
  delete document.documentElement.dataset.scene;
  delete document.documentElement.dataset.solarTerm;
});
</script>

<template>
  <div class="oriental-art-layer" :class="layerClass" aria-hidden="true">
    <div v-if="hasSlot('header')" class="art-slot art-slot--header" />
    <div v-if="hasSlot('lower_scroll')" class="art-slot art-slot--lower-scroll" />
    <div v-if="hasSlot('solar_term')" class="season-caption">
      <span class="season-caption__spring">春·花信</span>
      <span class="season-caption__summer">夏·荷风</span>
      <span class="season-caption__autumn">秋·桂月</span>
      <span class="season-caption__winter">冬·梅雪</span>
      <small v-if="solarTerm">{{ solarTerm }}</small>
    </div>
  </div>
</template>

<style scoped>
.oriental-art-layer {
  position: fixed;
  z-index: 0;
  inset: 84px 0 0 252px;
  overflow: hidden;
  pointer-events: none;
  user-select: none;
}

.oriental-art-layer.art-standalone {
  inset: 0;
}

.art-slot {
  position: absolute;
  background-repeat: no-repeat;
  background-position: center;
  background-size: contain;
  pointer-events: none;
  transform: translate3d(
    var(--term-art-x, 0),
    var(--term-art-y, 0),
    0
  ) scale(var(--term-art-scale, 1));
  transform-origin: center bottom;
  transition: opacity 160ms ease;
}

.art-slot--header {
  top: var(--scene-header-top, 0px);
  right: var(--scene-header-right, 0%);
  left: var(--scene-header-left, 23%);
  height: var(--scene-header-height, 194px);
  background-image: var(--scene-header-art, var(--season-header-landscape-art));
  background-position: var(--scene-header-position, center bottom);
  /* 保持 1800×360 原始画幅比例，禁止为了填满槽位而压扁山体或拉长亭台。 */
  background-size: var(--scene-header-size, contain);
  opacity: var(--scene-header-opacity, var(--season-header-opacity, 0.48));
  /*
   * 页头画卷必须像墨色自然渗入宣纸，而不是一张矩形图片贴在页面上。
   * 羽化只改变透明通道，不改变图片尺寸，因此不会压缩山体或亭台。
   */
  -webkit-mask-image: var(
    --scene-header-mask,
    linear-gradient(
      90deg,
      transparent 0%,
      #000 var(--scene-header-fade-left, 9%),
      #000 var(--scene-header-fade-right, 91%),
      transparent 100%
    )
  );
  mask-image: var(
    --scene-header-mask,
    linear-gradient(
      90deg,
      transparent 0%,
      #000 var(--scene-header-fade-left, 9%),
      #000 var(--scene-header-fade-right, 91%),
      transparent 100%
    )
  );
}

.art-slot--lower-scroll {
  right: var(--scene-lower-right, 0%);
  bottom: var(--scene-lower-bottom, 0px);
  left: var(--scene-lower-left, 0%);
  height: var(--scene-lower-height, min(52vh, 500px));
  background-image: var(--scene-lower-art, var(--season-lower-scroll-art));
  background-position: var(--scene-lower-position, center bottom);
  /*
   * 底景只接入横向长卷素材。宽度铺满、垂直方向自动缩放，保持原始画幅比例；
   * 禁止使用 100% 100% 拉伸，也不再让高幅素材缩成页面中央的一小块。
   */
  background-size: var(--scene-lower-size, 100% auto);
  opacity: var(--scene-lower-opacity, var(--season-lower-opacity, 0.45));
  /*
   * 场景源图在左右边缘保留了透明留白，但不同画幅的 Alpha 收口位置并不一致。
   * 在统一画布再做一次宽幅羽化，消除任务、收件和管理页面的竖向断边。
   */
  -webkit-mask-image: var(
    --scene-lower-mask,
    linear-gradient(
      90deg,
      transparent 0%,
      #000 var(--scene-lower-fade-left, 8%),
      #000 var(--scene-lower-fade-right, 92%),
      transparent 100%
    )
  );
  mask-image: var(
    --scene-lower-mask,
    linear-gradient(
      90deg,
      transparent 0%,
      #000 var(--scene-lower-fade-left, 8%),
      #000 var(--scene-lower-fade-right, 92%),
      transparent 100%
    )
  );
  /* 底部长卷必须始终覆盖整条主内容区；节气只调整纵向气韵和轻微缩放，不横移露边。 */
  transform: translate3d(
    0,
    var(--term-lower-y, 0),
    0
  ) scale(var(--term-lower-scale, 1));
}

.season-caption {
  position: absolute;
  top: 132px;
  right: 4.5%;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 3px 7px;
  color: rgba(142, 28, 20, 0.78);
  font-family: var(--serif);
  font-size: 11px;
  letter-spacing: 0.12em;
  background: rgba(247, 241, 231, 0.68);
  border: 1px solid rgba(180, 35, 24, 0.3);
}

.season-caption > span {
  display: none;
}

.season-caption small {
  color: rgba(92, 76, 66, 0.76);
  font-size: 10px;
  letter-spacing: 0.08em;
}

:global(html[data-season="spring"]) .season-caption__spring,
:global(html[data-season="summer"]) .season-caption__summer,
:global(html[data-season="autumn"]) .season-caption__autumn,
:global(html[data-season="winter"]) .season-caption__winter {
  display: inline;
}

:global(html[data-art-level="reduced"]) .oriental-art-layer {
  display: none;
}

:global(html[data-reduce-motion="true"]) .art-slot {
  transition-duration: 0.01ms !important;
}

@media (max-width: 1440px) {
  .art-slot--header {
    left: var(--scene-header-left-compact, var(--scene-header-left, 23%));
    height: var(--scene-header-height-compact, var(--scene-header-height, 168px));
  }

  .art-slot--lower-scroll {
    height: var(--scene-lower-height-compact, var(--scene-lower-height, min(48vh, 430px)));
  }
}

@media (max-height: 820px) {
  .art-slot--header {
    height: var(--scene-header-height-short, 148px);
  }

  .art-slot--lower-scroll {
    height: var(--scene-lower-height-short, min(44vh, 340px));
  }

  .season-caption {
    top: 112px;
  }
}
</style>
