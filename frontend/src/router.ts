import { createRouter, createWebHistory } from "vue-router";
import HomePage from "./pages/HomePage.vue";
import CreatePage from "./pages/CreatePage.vue";
import JoinPage from "./pages/JoinPage.vue";
import ChatPage from "./pages/ChatPage.vue";
import BoardPage from "./pages/BoardPage.vue";
import BottomlinesPage from "./pages/BottomlinesPage.vue";
import AnswerPage from "./pages/AnswerPage.vue";
import HistoryPage from "./pages/HistoryPage.vue";
import ConfirmPage from "./pages/ConfirmPage.vue";
import ConcernPage from "./pages/ConcernPage.vue";
import ManagePage from "./pages/ManagePage.vue";
import MembersPage from "./pages/MembersPage.vue";
import ResultPage from "./pages/ResultPage.vue";

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", name: "home", component: HomePage },
    { path: "/negotiations/new", name: "create", component: CreatePage },
    {
      path: "/join/:negotiationId/:inviteToken",
      name: "join",
      component: JoinPage,
    },
    { path: "/n/:id", name: "board", component: ChatPage },
    { path: "/n/:id/board", name: "boardClassic", component: BoardPage },
    { path: "/n/:id/me/bottomlines", name: "bottomlines", component: BottomlinesPage },
    { path: "/n/:id/me/answer", name: "answer", component: AnswerPage },
    { path: "/n/:id/me/history", name: "history", component: HistoryPage },
    { path: "/n/:id/me/confirm", name: "confirm", component: ConfirmPage },
    { path: "/n/:id/me/concern", name: "concern", component: ConcernPage },
    { path: "/n/:id/members", name: "members", component: MembersPage },
    { path: "/n/:id/manage", name: "manage", component: ManagePage },
    { path: "/n/:id/result", name: "result", component: ResultPage },
    { path: "/:pathMatch(.*)*", redirect: "/" },
  ],
  scrollBehavior() {
    return { top: 0 };
  },
});

export default router;
