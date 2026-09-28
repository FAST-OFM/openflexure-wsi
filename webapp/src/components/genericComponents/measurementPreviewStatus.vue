<template>
  <div v-if="status?.holding" class="measurement-preview-status" role="status">
    {{ status.active ? "R/G measurement — waiting for next WHITE" : "Waiting for fresh WHITE" }}
  </div>
</template>

<script>
export default {
  name: "MeasurementPreviewStatus",
  props: { enabled: { type: Boolean, default: false } },
  data: () => ({ status: null, timer: null, epoch: 0, busy: false }),
  computed: {
    available() {
      return Boolean(this.thingDescription?.("camera")?.properties?.measurement_preview_status);
    },
  },
  watch: {
    enabled: "restart",
    available: "restart",
  },
  mounted() {
    this.restart();
  },
  beforeUnmount() {
    clearInterval(this.timer);
    this.epoch++;
  },
  methods: {
    restart() {
      clearInterval(this.timer);
      this.epoch++;
      this.status = null;
      if (!this.enabled || !this.available) return;
      this.poll();
      this.timer = setInterval(this.poll, 1000);
    },
    async poll() {
      if (this.busy || document.hidden || !this.enabled || !this.available) return;
      const epoch = this.epoch;
      this.busy = true;
      try {
        const status = await this.readThingProperty("camera", "measurement_preview_status", true);
        if (epoch === this.epoch) this.status = status;
      } catch {
        if (epoch === this.epoch) this.status = null;
      } finally {
        this.busy = false;
      }
    },
  },
};
</script>

<style scoped>
.measurement-preview-status {
  position: absolute;
  bottom: 0.5rem;
  left: 0.5rem;
  right: 0.5rem;
  padding: 0.3rem 0.5rem;
  color: #fff;
  background: #252525e6;
  font-size: 0.8rem;
  pointer-events: none;
}
</style>
