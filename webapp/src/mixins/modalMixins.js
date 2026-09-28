/**
 * @fileoverview
 * Global mixin providing modal functionality.
 *
 * This mixin is registered globally in `main.js` using. Do not
 * manually import it in components.
 */

import UIkit from "uikit";
import { useSettingsStore } from "@/stores/settings.js";

export default {
  methods: {
    modalConfirm: function (modalText) {
      // force OK to be capitalised
      UIkit.modal.i18n = { ok: "OK", cancel: "Cancel" };

      var showModal = function (resolve, reject) {
        UIkit.modal.confirm(modalText, { stack: true }).then(
          function () {
            resolve();
          },
          function () {
            reject();
          },
        );
      };
      return new Promise(showModal);
    },

    modalNotify: function (message, status = "success") {
      UIkit.notification({
        message: message,
        status: status,
      });
    },

    modalDialog: function (title, message) {
      UIkit.modal.dialog(
        `
        <button class="uk-modal-close-default" type="button" uk-close></button>
        <div class="uk-modal-header">
          <h2 class="uk-modal-title">${title}</h2>
        </div>
        <div class="uk-modal-body">
          <p>${message}</p>
        </div>
      `,
        { stack: true },
      );
    },

    modalError(set_error) {
      const store = useSettingsStore();
      var errormsg = this.getErrorMessage(set_error);
      store.error = errormsg;
      UIkit.notification({
        message: `${errormsg}`,
        status: "danger",
      });
    },

    getErrorMessage: function (error) {
      // Format the error.
      let data = this.getErrorData(error);
      // Get error data may get an object. Handle edge cases and if not try to use
      // JSON to get the best string. This stops "objectObject" showing as an error.
      if (data === null) return "null";
      if (data === undefined) return "undefined";
      if (typeof data === "string") return data;
      try {
        return JSON.stringify(data, null, 2);
      } catch {
        return String(data);
      }
    },
    getErrorData: function (data_error) {
      // If a response was obtained, extract the most specific message
      if (data_error.response) {
        // If the response is a nicely formatted JSON response from the server
        if (data_error.response.data.message) {
          return data_error.response.data.message;
        }
        if (data_error.response.data.detail) {
          try {
            return data_error.response.data.detail[0].msg;
          } catch {
            return data_error.response.data.detail;
          }
        }
        // If the response is just some generic error response
        if (data_error.response.data) {
          return data_error.response.data;
        }
        return data_error.response;
      }
      // If we have an error object with a message, use that
      if (data_error.message) return data_error.message;
      // At this point just formatting the whole error object is the best we can do.
      return data_error;
    },

    showModalElement: function (element) {
      UIkit.modal(element).show();
    },

    hideModalElement: function (element) {
      UIkit.modal(element).hide();
    },

    toggleModalElement: function (element) {
      UIkit.modal(element).toggle();
    },
  },
};
