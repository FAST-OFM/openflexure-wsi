<template>
  <div id="captureControl">
    <select
      id="saveLocation"
      v-model="saveLocation"
      class="uk-select uk-form-small uk-width-1-1"
      aria-label="Capture destination"
      title="Capture destination"
    >
      <option value="gallery">Save to gallery</option>
      <option value="download">Download</option>
    </select>

    <div class="uk-margin-small-top">
      <action-button
        thing="camera"
        action="capture"
        :submit-data="submitData"
        submit-label="Capture"
        :submit-on-event="'globalCaptureEvent'"
        @response="handleCaptureResponse"
        @error="modalError"
      />
    </div>
  </div>
</template>

<script>
import axios from "axios";
import ActionButton from "@/components/labThingsComponents/actionButton.vue";

export default {
  name: "CaptureControl",

  components: {
    ActionButton,
  },

  data: function () {
    return {
      saveLocation: "gallery",
    };
  },

  computed: {
    submitData() {
      return {
        capture_mode: "standard",
        retain_image: this.saveLocation === "gallery",
      };
    },
  },

  methods: {
    handleCaptureResponse: async function (response) {
      // Retrieve the captured image and save it
      if (this.saveLocation === "gallery") return;
      let imageUri = response.output.href;
      if (!imageUri) {
        this.modalError("No image URI returned from capture task.");
        return;
      }
      // To save the returned data, we make a virtual link and click it
      let imageResponse = await axios.get(imageUri, { responseType: "blob" });
      const url = window.URL.createObjectURL(new Blob([imageResponse.data]));
      const link = document.createElement("a");
      link.href = url;
      link.setAttribute("download", `OFM_${new Date().toISOString()}.jpeg`);
      document.body.appendChild(link);
      link.click();
    },
  },
};
</script>
