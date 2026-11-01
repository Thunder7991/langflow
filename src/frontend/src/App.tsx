import "@xyflow/react/dist/style.css";
import { Suspense, useLayoutEffect } from "react";
import { RouterProvider } from "react-router-dom";
import { LoadingPage } from "./pages/LoadingPage";
import router from "./routes";
import { useDarkStore } from "./stores/darkStore";

export default function App() {
  const dark = useDarkStore((state) => state.dark);
  useLayoutEffect(() => {
    if (!dark) {
      document.getElementById("body")!.classList.remove("dark");
    } else {
      document.getElementById("body")!.classList.add("dark");
    }
  }, [dark]);
  return (
    <Suspense fallback={<LoadingPage />}>
      <RouterProvider router={router} />
    </Suspense>
  );
}
