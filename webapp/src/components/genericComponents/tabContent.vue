<template>
  <div
    v-if="!(requireConnection && !ready)"
    :hidden="currentTab != tabID"
    class="ofm-tab uk-width-expand uk-height-1-1"
  >
    <slot></slot>
  </div>
</template>

<script>
import { mapState } from "pinia";
import { useSettingsStore } from "@/stores/settings";

export default {
  name: "TabContent",

  props: {
    tabID: {
      type: String,
      required: true,
    },
    currentTab: {
      type: String,
      required: true,
    },
    requireConnection: Boolean,
  },
  computed: {
    ...mapState(useSettingsStore, ["ready"]),
  },

  methods: {},
};
</script>

<style lang="less" scoped>
.section-heading {
  display: block;
  font-size: 12px;
  text-transform: uppercase;
  line-height: 20px;
  cursor: default;
}
</style>
