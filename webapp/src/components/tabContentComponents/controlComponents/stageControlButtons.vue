<template>
  <div class="uk-flex uk-flex-center uk-flex-middle uk-margin">
    <div
      class="dpad-grid"
      :class="{
        'dpad-only': showDpad && !showFocusControls,
        'focus-only': !showDpad && showFocusControls,
        'both-controls': showDpad && showFocusControls,
      }"
    >
      <button
        v-if="showDpad"
        id="up-button"
        :disabled="disabled || !axesEnabled.y"
        :aria-disabled="disabled || busy || !axesEnabled.y"
        class="uk-button uk-button-primary dpad-btn"
        @keydown.enter.prevent="keyboardHold($event, 0, 1, 0)"
        @keydown.space.prevent="keyboardHold($event, 0, 1, 0)"
        @keyup="jogStop()"
        @lostpointercapture="jogStop()"
        @pointerdown="jog($event, 0, 1, 0)"
        @pointerup="jogStop()"
        @pointercancel="jogStop()"
      >
        <span class="material-symbols-outlined sync-icon"> arrow_upward </span>
      </button>

      <button
        v-if="showDpad"
        id="left-button"
        :disabled="disabled || !axesEnabled.x"
        :aria-disabled="disabled || busy || !axesEnabled.x"
        class="uk-button uk-button-primary dpad-btn"
        @keydown.enter.prevent="keyboardHold($event, -1, 0, 0)"
        @keydown.space.prevent="keyboardHold($event, -1, 0, 0)"
        @keyup="jogStop()"
        @lostpointercapture="jogStop()"
        @pointerdown="jog($event, -1, 0, 0)"
        @pointerup="jogStop()"
        @pointercancel="jogStop()"
      >
        <span class="material-symbols-outlined sync-icon"> arrow_back </span>
      </button>

      <button
        v-if="showDpad"
        id="right-button"
        :disabled="disabled || !axesEnabled.x"
        :aria-disabled="disabled || busy || !axesEnabled.x"
        class="uk-button uk-button-primary dpad-btn"
        @keydown.enter.prevent="keyboardHold($event, 1, 0, 0)"
        @keydown.space.prevent="keyboardHold($event, 1, 0, 0)"
        @keyup="jogStop()"
        @lostpointercapture="jogStop()"
        @pointerdown="jog($event, 1, 0, 0)"
        @pointerup="jogStop()"
        @pointercancel="jogStop()"
      >
        <span class="material-symbols-outlined sync-icon"> arrow_forward </span>
      </button>

      <button
        v-if="showDpad"
        id="down-button"
        :disabled="disabled || !axesEnabled.y"
        :aria-disabled="disabled || busy || !axesEnabled.y"
        class="uk-button uk-button-primary dpad-btn"
        @keydown.enter.prevent="keyboardHold($event, 0, -1, 0)"
        @keydown.space.prevent="keyboardHold($event, 0, -1, 0)"
        @keyup="jogStop()"
        @lostpointercapture="jogStop()"
        @pointerdown="jog($event, 0, -1, 0)"
        @pointerup="jogStop()"
        @pointercancel="jogStop()"
      >
        <span class="material-symbols-outlined sync-icon"> arrow_downward </span>
      </button>

      <button
        v-if="showFocusControls"
        id="focus-out-button"
        :disabled="disabled || !axesEnabled.z"
        :aria-disabled="disabled || busy || !axesEnabled.z"
        class="uk-button uk-button-primary dpad-btn"
        @keydown.enter.prevent="keyboardHold($event, 0, 0, -1)"
        @keydown.space.prevent="keyboardHold($event, 0, 0, -1)"
        @keyup="jogStop()"
        @lostpointercapture="jogStop()"
        @pointerdown="jog($event, 0, 0, -1)"
        @pointerup="jogStop()"
        @pointercancel="jogStop()"
      >
        <span class="material-symbols-outlined sync-icon"> remove </span>
      </button>

      <button
        v-if="showFocusControls"
        id="focus-in-button"
        :disabled="disabled || !axesEnabled.z"
        :aria-disabled="disabled || busy || !axesEnabled.z"
        class="uk-button uk-button-primary dpad-btn"
        @keydown.enter.prevent="keyboardHold($event, 0, 0, 1)"
        @keydown.space.prevent="keyboardHold($event, 0, 0, 1)"
        @keyup="jogStop()"
        @lostpointercapture="jogStop()"
        @pointerdown="jog($event, 0, 0, 1)"
        @pointerup="jogStop()"
        @pointercancel="jogStop()"
      >
        <span class="material-symbols-outlined sync-icon"> add </span>
      </button>
    </div>
  </div>
</template>

<script>
import { eventBus } from "@/eventBus.js";
import { mapWritableState } from "pinia";
import { useSettingsStore } from "@/stores/settings.js";

export default {
  name: "StageControlButtons",
  props: {
    bounded: { type: Boolean, default: false },
    disabled: { type: Boolean, default: false },
    // Busy blocks NEW presses, but must not cancel the hold awaiting its own step.
    busy: { type: Boolean, default: false },
    axesEnabled: { type: Object, default: () => ({ x: true, y: true, z: true }) },
    stepUnits: { type: Object, default: () => ({ x: 0, y: 0, z: 0 }) },
    moveStep: { type: Function, default: null },
    repeatDelayMs: { type: Number, default: 100 },
    showDpad: {
      type: Boolean,
      default: true,
    },
    showFocusControls: {
      type: Boolean,
      default: true,
    },
  },
  data: () => ({
    jogIntervalId: null,
    jogDistance: 600,
    jogTime: 300,
    held: false,
    holdGeneration: 0,
    holdTimer: null,
    finishDelay: null,
  }),

  computed: {
    ...mapWritableState(useSettingsStore, ["navigationInvert"]),
  },

  watch: {
    disabled(value) {
      if (value) this.stopHold();
    },
    stepUnits: {
      deep: true,
      handler() {
        this.stopHold();
      },
    },
  },

  mounted() {
    window.addEventListener("blur", this.stopHold);
    document.addEventListener("visibilitychange", this.visibilityChanged);
  },

  beforeUnmount() {
    this.stopHold();
    clearInterval(this.jogIntervalId);
    window.removeEventListener("blur", this.stopHold);
    document.removeEventListener("visibilitychange", this.visibilityChanged);
  },

  methods: {
    stopHold() {
      this.held = false;
      this.holdGeneration += 1;
      clearTimeout(this.holdTimer);
      this.finishDelay?.();
      this.finishDelay = null;
    },
    visibilityChanged() {
      if (document.hidden) this.stopHold();
    },
    keyboardHold(event, x, y, z) {
      if (!event.repeat && this.bounded) this.beginHold(x, y, z);
    },
    async beginHold(x, y, z) {
      if (!this.bounded || this.disabled || this.busy || this.held || !this.moveStep) return;
      const direction = { x, y, z };
      if (Object.entries(direction).some(([axis, value]) => value && !this.axesEnabled[axis]))
        return;
      if (
        Object.entries(direction).some(
          ([axis, value]) =>
            value && (!Number.isSafeInteger(this.stepUnits[axis]) || this.stepUnits[axis] <= 0),
        )
      )
        return;
      const delta = {
        x: x * this.stepUnits.x * (this.navigationInvert.x ? -1 : 1),
        y: y * this.stepUnits.y * (this.navigationInvert.y ? -1 : 1),
        z: z * this.stepUnits.z,
      };
      this.held = true;
      const generation = ++this.holdGeneration;
      try {
        while (this.held && !this.disabled && generation === this.holdGeneration) {
          // Never queue ahead: completion/readback/settle precede the next request.
          if ((await this.moveStep(delta)) !== true) break;
          if (!this.held || generation !== this.holdGeneration) break;
          await new Promise((resolve) => {
            this.finishDelay = resolve;
            this.holdTimer = setTimeout(resolve, this.repeatDelayMs);
          });
          this.finishDelay = null;
        }
      } finally {
        if (generation === this.holdGeneration) this.stopHold();
      }
    },
    /**
     * Jog d-pad and focus buttons.
     *
     * This is a similar to the function in App.vue, however it uses an Interval rather
     * than the one in App.vue that uses key repeats.
     */
    jog(pointerEvent, x, y, z) {
      if (this.disabled || (this.bounded && this.busy)) return;
      // Only respond to primary button (left mouse / primary touch)
      if (pointerEvent.button !== 0) return;
      if (this.bounded) {
        pointerEvent.currentTarget.setPointerCapture(pointerEvent.pointerId);
        this.beginHold(x, y, z);
        return;
      }

      if (this.jogIntervalId) {
        clearInterval(this.jogIntervalId);
      }

      // Designate this element to get the pointers next pointerup event wherever that
      // pointer is.
      pointerEvent.target.setPointerCapture(pointerEvent.pointerId);

      let invokeJog = () =>
        this.invokeAction("stage", "jog", {
          x: x * this.jogDistance * (this.navigationInvert.x ? -1 : 1),
          y: y * this.jogDistance * (this.navigationInvert.y ? -1 : 1),
          z: z * this.jogDistance,
        });
      invokeJog();
      this.jogIntervalId = setInterval(invokeJog, this.jogTime);
    },
    /**
     * Stop jogging from d-pad and focus buttons.
     *
     * This is a similar to the function in App.vue, but it is designed to clear the
     * interval used with the d-pad and focus buttons.
     */
    jogStop() {
      if (this.bounded) {
        this.stopHold();
        return;
      }
      if (this.jogIntervalId) {
        clearInterval(this.jogIntervalId);
      }
      this.invokeAction("stage", "jog", { stop: true });
      setTimeout(() => {
        eventBus.emit("globalUpdatePositionEvent");
      }, 100);
    },
  },
};
</script>

<style scoped>
.dpad-grid {
  display: grid;
  grid-template-columns: repeat(3, 40px);
  gap: 1px;
  justify-content: center;
  align-items: center;
}

.both-controls {
  grid-template-rows: 40px 40px 40px 20px 40px;
}

.dpad-only {
  grid-template-rows: 40px 40px 40px;
}

.focus-only {
  grid-template-rows: 40px;
}

/* Place buttons within grid */
.dpad-grid #up-button {
  grid-column: 2;
  grid-row: 1;
}

.dpad-grid #left-button {
  grid-column: 1;
  grid-row: 2;
}

.dpad-grid #right-button {
  grid-column: 3;
  grid-row: 2;
}

.dpad-grid #down-button {
  grid-column: 2;
  grid-row: 3;
}

.both-controls #focus-out-button {
  grid-column: 1;
  grid-row: 5;
}

.both-controls #focus-in-button {
  grid-column: 3;
  grid-row: 5;
}

.focus-only #focus-out-button {
  grid-column: 1;
  grid-row: 1;
}

.focus-only #focus-in-button {
  grid-column: 3;
  grid-row: 1;
}

.dpad-btn {
  width: 40px;
  height: 40px;
  justify-content: center;
  align-items: center;
  display: flex;
}

/* Keep pointer capture/release events during an in-flight held step. Native
   disabled is reserved for safety gates; busy uses ARIA + guarded handlers. */
.dpad-btn[aria-disabled="true"] {
  opacity: 0.45;
  cursor: not-allowed;
}
</style>
