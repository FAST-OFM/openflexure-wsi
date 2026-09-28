<template>
  <div class="uk-card">
    <div class="uk-card-body" :class="{ 'thumbnail-only-card': thumbnailOnly }">
      <div
        class="uk-card-media-top uk-flex uk-flex-center uk-flex-middle"
        :class="{ 'thumbnail-only-container': thumbnailOnly }"
      >
        <div class="uk-padding-remove">
          <img
            id="thumbnail-stitched-image"
            :class="[viewerAvailable ? 'clickable' : 'disabled', thumbnailOnly && 'thumbnail-only']"
            class="thumbnail-fit"
            :src="thumbnailPath"
            onerror="this.src = '/titleiconpink.svg'"
            @click="viewerAvailable && requestViewer()"
          />
        </div>
      </div>
      <template v-if="!thumbnailOnly">
        <h3 class="uk-card-title gallery-card-title">{{ itemData.name }}</h3>
        <div class="button-container">
          <!-- Scans require their own download buttons, as their logic works differently.
          Scans can either download their entire ZIP, or just a JPEG from downloadURL -->
          <div v-if="itemData.card_type === 'Scan'" class="uk-button-group gallery-card-buttons">
            <action-button
              class="uk-width-1-2"
              thing="smart_scan"
              action="download_zip"
              submit-label="Download All"
              :can-terminate="false"
              :submit-data="{ scan_name: itemData.name }"
              :button-primary="true"
              @response="downloadZipFile"
              @error="modalError"
            />
            <EndpointButton
              class="uk-width-1-2"
              :button-primary="true"
              :is-disabled="!itemData.gallery_info.stitch_available"
              :url="downloadUrl"
              :button-label="downloadLabel"
            />
          </div>
          <EndpointButton
            v-if="canDownload()"
            class="uk-width-1"
            :button-primary="true"
            :url="downloadUrl"
            button-label="Download"
          />
          <button class="uk-button uk-button-default uk-width-1-1" @click="deleteItem">
            Delete
          </button>
          <!-- Note that action button can't just use @finished, as that would refresh when the action
          is finished, not when the modal closes. The refresh would orphan the modal, making it
          impossible to close -->
          <template v-if="itemData.card_type === 'Scan'">
            <action-button
              v-if="canStitch()"
              submit-label="Stitch Images"
              thing="smart_scan"
              action="stitch_scan"
              :can-terminate="true"
              :submit-data="{ scan_name: itemData.name }"
              :button-primary="false"
              :modal-progress="true"
              @error="modalError"
              @action-modal-closed="stitchFinished"
            />
          </template>
          <button
            v-if="canView()"
            class="uk-button uk-button-default uk-width-1-1"
            @click="requestViewer"
          >
            View
          </button>
        </div>
        <div class="item-info">
          <ul>
            <li v-if="itemData.gallery_info.number_of_images">
              {{ itemData.gallery_info.number_of_images }} images
            </li>
            <li>Created: {{ formatDate(itemData.created) }}</li>
            <li v-if="itemData.gallery_info.duration">
              Duration: {{ formatDuration(itemData.gallery_info.duration) }}
            </li>
          </ul>
        </div>
      </template>
      <div
        v-if="itemData.card_type === 'Scan'"
        :class="{ 'thumbnail-only-warning': thumbnailOnly }"
      >
        <ul>
          <li v-if="itemData.gallery_info.number_of_images < 2" class="warning-msg">
            Not enough images to stitch
          </li>
          <li
            v-else-if="!itemData.gallery_info.dzi && itemData.gallery_info.stitch_available"
            class="alert-msg"
          >
            Interactive preview not available
          </li>
          <li v-else-if="!itemData.gallery_info.stitch_available" class="alert-msg">
            High quality stitch not available
          </li>
        </ul>
      </div>
    </div>
  </div>
</template>

<script>
import axios from "axios";
import actionButton from "../../labThingsComponents/actionButton.vue";
import EndpointButton from "../../labThingsComponents/endpointButton.vue";
import { mapState } from "pinia";
import { useSettingsStore } from "@/stores/settings.js";
import { formatDate, formatDuration } from "@/js_utils/formatter.mjs";

// Export main app
export default {
  name: "GalleryCard",
  components: {
    actionButton,
    EndpointButton,
  },

  props: {
    itemData: {
      type: Object,
      required: true,
    },
    thumbnailOnly: {
      type: Boolean,
      default: false,
      required: false,
    },
  },

  emits: ["viewer-requested", "update-requested"],

  computed: {
    ...mapState(useSettingsStore, ["baseUri"]),
    dataUri() {
      return `${this.baseUri}/data/`;
    },
    downloadUrl() {
      if (this.itemData.card_type === "Scan") {
        const stitchedPath =
          this.itemData.gallery_info.stitched_tiff || this.itemData.gallery_info.stitched_jpeg;
        if (!stitchedPath) return "";
        return `${this.dataUri}/${this.itemData.path}/${stitchedPath}`;
      }
      return `${this.dataUri}/${this.itemData.path}`;
    },
    downloadLabel() {
      return this.itemData.gallery_info.stitched_tiff ? "Download BigTIFF" : "Download JPEG";
    },
    thumbnailPath() {
      return `${this.dataUri}/${this.itemData.thumbnail_source}`;
    },
    /**
     * Return True if there is a viewer available for this card's data.
     */
    viewerAvailable() {
      if (this.itemData.card_type === "Scan") return Boolean(this.itemData?.gallery_info.dzi);
      return true;
    },
  },

  methods: {
    formatDate,
    formatDuration,
    stitchFinished() {
      this.$emit("update-requested");
    },
    requestViewer() {
      // Notify parent that thumbnail was clicked
      this.$emit("viewer-requested", this.itemData);
    },
    async deleteItem() {
      try {
        await this.modalConfirm(`Are you sure you want to delete ${this.itemData.name}?`);
        await axios.delete(`${this.baseUri}/${this.itemData.delete_endpoint}`);
        this.$emit("update-requested");
        this.modalNotify(`Deleted ${this.itemData.name}`);
      } catch (e) {
        // if the confirmation was cancelled, it's rejected with null error
        if (e) this.modalError(e);
      }
    },
    canDownload() {
      // Returns true if not a scan, as scans have their own buttons.
      return this.itemData.card_type !== "Scan";
    },
    canStitch() {
      return (
        (!this.itemData.gallery_info.stitch_available &&
          this.itemData.gallery_info.number_of_images > 1) ||
        (this.itemData.gallery_info.stitch_available && !this.itemData.gallery_info.dzi)
      );
    },
    canView() {
      if (this.itemData.card_type !== "Scan") return true;
      return Boolean(this.itemData.gallery_info.dzi);
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
<style lang="less" scoped>
ul {
  display: block;
  text-align: center;
  list-style-type: none;
  margin: 5px 0 10px;
  padding: 0;
}

.warning-msg {
  color: red;
  text-align: center;
  font-weight: bold;
}

.alert-msg {
  color: orange;
  text-align: center;
  font-weight: bold;
}

.gallery-card-buttons {
  width: 100%;
}

.gallery-card-title {
  text-align: center;
}

.thumbnail-only-card {
  width: auto;
}

.thumbnail-only-container {
  height: 150px;
  width: 150px;
}

.thumbnail-only-warning {
  width: 150px;
}

.thumbnail-fit {
  max-height: 120px;
  max-width: 240px;
  object-fit: contain;
  overflow-y: hidden;
}

.thumbnail-fit.thumbnail-only {
  max-height: 150px;
  max-width: 150px;
}

.clickable {
  cursor: pointer;
}

.disabled {
  cursor: not-allowed;
}
</style>
