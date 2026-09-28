<template>
  <div v-if="!dismissed" class="portrait-warning">
    <button
      class="portrait-warning-close"
      type="button"
      aria-label="Close"
      @click="showConfirmation = true"
    >
      <span class="material-symbols-outlined">close</span>
    </button>

    <div class="portrait-warning-content">
      <span class="material-symbols-outlined rotate-device-icon"> screen_rotation </span>

      <template v-if="!showConfirmation">
        <h2>Please rotate your device</h2>
        <p>This app is designed to be used in landscape orientation.</p>
      </template>

      <template v-else>
        <h2>Continue in portrait mode?</h2>
        <p>
          The app is designed for landscape orientation, so some parts of the interface may not
          appear or work as intended.
        </p>

        <div class="portrait-warning-actions">
          <button
            class="uk-button uk-button-default"
            type="button"
            @click="showConfirmation = false"
          >
            Cancel
          </button>

          <button class="uk-button uk-button-primary" type="button" @click="dismissed = true">
            Continue
          </button>
        </div>
      </template>
    </div>
  </div>
</template>

<script>
export default {
  name: "PortraitWarning",

  data() {
    return {
      dismissed: false,
      showConfirmation: false,
    };
  },
};
</script>

<style lang="less">
.portrait-warning {
  display: none;
}

// Only show on actual touch devices in portrait mode.
// Deliberately strict so desktop/laptop windows aren't affected.
@media screen and (width <= 480px) and (orientation: portrait) and (hover: none) and (pointer: coarse) {
  .portrait-warning {
    position: fixed;
    inset: 0;
    z-index: 99999;
    display: flex;
    align-items: center;
    justify-content: center;
    background: #111;
    color: white;
    text-align: center;
    padding: 30px;
  }

  .portrait-warning-content {
    position: relative;
    max-width: 320px;
    padding: 40px 24px;
  }

  .material-symbols-outlined.rotate-device-icon {
    font-size: 64px;
  }

  .portrait-warning h2 {
    margin-bottom: 10px;
  }

  .portrait-warning p {
    margin: 0;
  }

  .portrait-warning-close {
    position: absolute;
    top: 20px;
    right: 20px;
    border: 0;
    background: transparent;
    color: inherit;
    cursor: pointer;
    padding: 5px;
  }

  .portrait-warning-close .material-symbols-outlined {
    font-size: 32px;
  }

  .portrait-warning-actions {
    display: flex;
    justify-content: center;
    gap: 10px;
    margin-top: 24px;
  }
}
</style>
