import { createApp } from "vue";
import "vant/lib/index.css";
import "./styles/prototype.css";
import "./styles/app.css";
import "./styles/theme-nocturne.css";
import "./styles/stage.css";
import "./styles/chat.css";
import App from "./App.vue";
import router from "./router";

createApp(App).use(router).mount("#app");
