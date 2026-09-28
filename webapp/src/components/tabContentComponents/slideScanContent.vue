<template>
  <actionTab
    thing="smart_scan"
    action="sample_scan"
    :task-id="taskId"
    :task-url="taskUrl"
    :task-info-title="infoPaneTitle"
    :task-info-stream="displayImageOnRight"
    :cancel-label="cancelLabel"
    @finished="onScanCompleted"
    @close-task="closeTask"
    @action-started-externally="startScanning"
  >
    <!-- MainView -->
    <div class="image-fit-wrapper">
      <img
        v-if="displayImageOnRight"
        id="last-stitched-image"
        class="image-fit"
        :src="lastStitchedImage"
      />
      <streamDisplay v-else :stream-id="setStreamId" />
    </div>
    <template #controls>
      <slideScanControls />
      <label class="uk-form-label" for="form-stacked-text">Sample ID</label>
      <div class="uk-form-controls">
        <input v-model="scan_name" class="uk-input uk-form-small" type="text" name="Scan Name" />
      </div>
      <div class="uk-margin">
        <action-button
          ref="smartScanButton"
          thing="smart_scan"
          action="sample_scan"
          :submit-data="{ scan_name: scan_name }"
          submit-label="Start Smart Scan"
          @task-started="startScanning"
        />
      </div>
    </template>
    <template #task-info>
      <div class="uk-width-1-1 uk-flex uk-flex-center">
        <div class="uk-width-2-3">
          <action-button
            v-if="scanComplete && imageCount >= 1"
            thing="smart_scan"
            action="download_zip"
            submit-label="Download ZIP"
            :can-terminate="false"
            :submit-data="{ scan_name: lastScanName }"
            :button-primary="true"
            @response="downloadZipFile"
            @error="modalError"
          />
        </div>
      </div>
      <div class="uk-margin-left info-headings">
        <h3 v-if="scanning" class="uk-margin-left uk-flex uk-flex-middle">
          <span>Scan ID: {{ lastScanName }}</span>
          <button
            v-if="lastScanName"
            class="scan-info-button uk-margin-small-left"
            type="button"
            title="View current scan settings"
            aria-label="View current scan settings"
            @click="openScanSettingsModal"
          >
            <span class="material-symbols-outlined">info</span>
          </button>
        </h3>
        <h3 v-if="scanning" class="uk-margin-left">Images captured: {{ imageCount }}</h3>
        <h3 v-if="scanning && scanDurationLabel" class="uk-margin-left">
          Scan duration: {{ scanDurationLabel }}
        </h3>
        <focus-scan-setup-status
          v-if="scanning"
          class="uk-margin-left"
          mode="live"
          :focus="scanStatusError ? null : scanDetails?.focus || null"
        />
        <p v-if="scanStatusError" class="uk-text-warning uk-margin-left">{{ scanStatusError }}</p>
      </div>

      <scanSettingsModal ref="scanSettingsModal" :scan-details="scanDetails" />
    </template>
  </actionTab>
</template>

<script>
import actionTab from "./actionTab.vue";
import slideScanControls from "./slideScanComponents/slideScanControls.vue";
import streamDisplay from "./streamContent.vue";
import ActionButton from "../labThingsComponents/actionButton.vue";
import scanSettingsModal from "./slideScanComponents/scanSettingsModal.vue";
import FocusScanSetupStatus from "./slideScanComponents/focusScanSetupStatus.vue";
import { formatDuration } from "@/js_utils/formatter.mjs";
import { mapState } from "pinia";
import { useSettingsStore } from "@/stores/settings.js";
export default {
  name: "SlideScanContent",

  components: {
    actionTab,
    slideScanControls,
    streamDisplay,
    ActionButton,
    scanSettingsModal,
    FocusScanSetupStatus,
  },

  data() {
    return {
      lastScanName: null,
      taskId: null,
      taskUrl: null,
      lastStitchedImage: null,
      imageCount: null,
      scanPhase: null,
      scanComplete: false,
      scan_name: "",
      // This adds the parent name as value for prop streamId
      setStreamId: this.$options.name,
      // Full LiveScanDetails from the latest poll. Passed straight through to ScanSettingsModal.
      scanDetails: null,
      scanStatusError: "",
      pollGeneration: 0,
      pollTimer: null,
    };
  },

  computed: {
    ...mapState(useSettingsStore, ["baseUri"]),
    scanning() {
      return this.taskId && this.taskUrl;
    },
    displayImageOnRight() {
      return this.scanning && this.lastStitchedImage !== null;
    },
    infoPaneTitle() {
      if (!this.displayImageOnRight) return null;
      return "Live stitching preview";
    },
    cancelLabel() {
      if (!this.scanPhase) return "Cancel";
      if (this.scanPhase === "Complete") return "Cancel";
      return `Cancel ${this.scanPhase}`;
    },

    scanDurationLabel() {
      if (!this.scanDetails) return null;
      return formatDuration(this.scanDetails.duration_seconds);
    },
  },

  beforeUnmount() {
    this.pollGeneration += 1;
    clearTimeout(this.pollTimer);
  },

  methods: {
    /**
     * Transition the UI into "scanning" mode and begin polling for scan updates.
     *
     * IMPORTANT:
     * This method does NOT start a scan on the server.
     *
     * The <ActionButton> component is responsible for:
     *  - initiating the scan action on the backend when the user clicks the button
     *  - detecting and resuming an already-running scan when the page loads
     *
     * As a result, this method may be invoked in two cases:
     *  1. Immediately after the user clicks "Start Smart Scan"
     *  2. Automatically on page load if <ActionButton> detects an ongoing scan
     *
     * This function only:
     *  - updates local UI state to reflect that scanning is in progress
     *  - clears any previous preview image
     *  - starts the polling loop that fetches scan progress and preview images
     */
    startScanning(id, url) {
      this.pollGeneration += 1;
      clearTimeout(this.pollTimer);
      this.lastStitchedImage = null;
      this.lastScanName = null;
      this.imageCount = 0;
      this.scanPhase = null;
      this.taskId = id;
      this.taskUrl = url;
      this.scanComplete = false;
      this.scanDetails = null;
      this.scanStatusError = "";
      this.pollTimer = setTimeout(() => this.pollScan(), 1000);
    },
    async onScanCompleted() {
      this.scanComplete = true;
      clearTimeout(this.pollTimer);
      await this.pollScan(true);
    },
    closeTask() {
      this.pollGeneration += 1;
      clearTimeout(this.pollTimer);
      this.taskId = null;
      this.taskUrl = null;
      this.lastStitchedImage = null;
      this.imageCount = null;
      this.scanPhase = null;
      this.scanComplete = false;
      this.scanDetails = null;
      this.scanStatusError = "";
    },
    async pollScan(finalRead = false) {
      if (!this.scanning || (this.scanComplete && !finalRead)) return;
      const generation = ++this.pollGeneration;
      try {
        const scanDetails = await this.readThingProperty(
          "smart_scan",
          "latest_scan_live_details",
          true,
        );
        if (generation !== this.pollGeneration) return;
        if (!scanDetails) throw new Error("Scan status readback is unavailable");
        this.scanDetails = scanDetails;
        this.scanStatusError = "";
        this.lastScanName = scanDetails.name;
        let mtime = scanDetails.stitch_timestamp;
        this.imageCount = scanDetails.image_count;
        this.scanPhase = scanDetails.scan_phase;

        if (mtime !== null) {
          this.lastStitchedImage = `${this.baseUri}/smart_scan/latest_preview_stitch.jpg?t=${mtime}`;
        }
      } catch {
        if (generation === this.pollGeneration) {
          this.scanStatusError = finalRead
            ? "Final focus status could not be confirmed."
            : "Current focus status could not be refreshed.";
        }
      } finally {
        if (generation === this.pollGeneration && !this.scanComplete) {
          this.pollTimer = setTimeout(() => this.pollScan(), 1000);
        }
      }
    },
    openScanSettingsModal() {
      this.$refs.scanSettingsModal.open();
    },
    async downloadZipFile(response) {
      const scan_name = response.input.scan_name;
      const filename = `${scan_name}_images.zip`;
      const url = response.output.href;
      const link = document.createElement("a");
      link.href = url;
      link.setAttribute("download", filename);
      document.body.appendChild(link);
      link.click();
    },
  },
};
</script>

<style scoped>
.info-headings h3 {
  margin-top: 20px;
  margin-bottom: 5px;
}

/* If one heading follows another don't have a large top margin. */
.info-headings h3 + h3 {
  margin-top: 5px;
}

.scan-info-button {
  background: none;
  border: none;
  padding: 0;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  color: inherit;
}
</style>
