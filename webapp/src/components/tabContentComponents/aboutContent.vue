<template>
  <!-- Grid managing tab content -->
  <div class="uk-height-1-1 view-component">
    <div class="uk-padding-small">
      <statusPane class="uk-width-large" />
    </div>
    <div class="uk-padding-small">
      <h2>Intended use</h2>
      <p>
        The OpenFlexure system, including hardware, software and peripherals, is intended solely for
        research, educational, training, and non-clinical use.<br />The OpenFlexure Microscope is
        not a certified medical device, and any use outside the stated intended purpose is the sole
        responsibility of the user.
      </p>
    </div>
    <div class="uk-padding-small">
      <h2>Fast OFM prototype extensions</h2>
      <p>
        This is a modified OpenFlexure Microscope Server build maintained by the independent Fast
        OFM project. It adds prototype motion-controller, illumination, red/green autofocus, sparse
        focus-prediction, scanning and WSI-output work. Fast OFM is not endorsed by or affiliated
        with the OpenFlexure project.
      </p>
      <p>
        The modifications are research prototypes and are not clinically validated. See
        <code>UPSTREAM.md</code>, <code>MODIFICATIONS.md</code> and <code>DISCLAIMER.md</code> in
        the source distribution for provenance, scope and limitations.
      </p>
    </div>
    <div class="uk-padding-small">
      <h2>Links</h2>
      <ul>
        <li>
          <a class="uk-link" target="_blank" href="https://openflexure.discourse.group/">
            Help and community support - OpenFlexure Forum
          </a>
        </li>
        <li>
          <a
            class="uk-link"
            target="_blank"
            href="https://gitlab.com/openflexure/openflexure-microscope-server/-/issues"
          >
            Report an issue
          </a>
        </li>
        <li>
          <a class="uk-link" target="_blank" href="https://openflexure.org">
            OpenFlexure Homepage
          </a>
        </li>
      </ul>
    </div>
  </div>
</template>

<script>
import statusPane from "./aboutComponents/statusPane.vue";

export default {
  name: "AboutContent",

  components: {
    statusPane,
  },
};
</script>
