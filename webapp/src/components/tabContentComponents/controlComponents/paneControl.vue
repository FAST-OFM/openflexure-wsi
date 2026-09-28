<template>
  <div id="paneControl" class="uk-padding-small">
    <positionControl v-if="stageAvailable" />
    <autofocusControl v-if="autofocusAvailable" />
    <captureControl />
  </div>
</template>

<script>
import captureControl from "./captureControl.vue";
import positionControl from "./positionControl.vue";
import autofocusControl from "./autofocusControl.vue";

export default {
  name: "PaneControl",

  components: {
    captureControl,
    positionControl,
    autofocusControl,
  },

  computed: {
    stageAvailable() {
      return this.thingAvailable("stage");
    },
    autofocusAvailable() {
      return this.thingAvailable("autofocus");
    },
  },
};
</script>
