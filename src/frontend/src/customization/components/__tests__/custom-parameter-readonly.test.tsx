import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ComponentProps } from "react";
import useAuthStore from "@/stores/authStore";
import { CustomParameterComponent } from "../custom-parameter";

let mockReadOnly = false;
jest.mock("@/contexts/permissionsContext", () => ({
  useIsFlowReadOnly: () => mockReadOnly,
}));
jest.mock("@/stores/flowStore", () => ({
  __esModule: true,
  default: (selector: (state: unknown) => unknown) =>
    selector({ currentFlow: { id: "own-flow" }, edges: [] }),
}));
// 仅替换无关的多控件分发树；输入框、展开弹窗和字段本身保持真实实现。
jest.mock("@/components/core/parameterRenderComponent", () => ({
  ParameterRenderComponent: (props: Record<string, unknown>) => {
    const TextArea =
      require("@/components/core/parameterRenderComponent/components/textAreaComponent").default;
    return (
      <TextArea {...props} id="parser-template" value={props.templateValue} />
    );
  },
}));

const props: ComponentProps<typeof CustomParameterComponent> = {
  handleOnNewValue: jest.fn(),
  handleNodeClass: jest.fn(),
  name: "pattern",
  nodeId: "Parser-live",
  inputId: {
    fieldName: "pattern",
    id: "Parser-live",
    inputTypes: [],
    type: "str",
  },
  templateData: { type: "str", value: "saved {text}" },
  templateValue: "saved {text}",
  showParameter: true,
  inspectionPanel: false,
  editNode: false,
  nodeClass: {
    template: {},
    display_name: "Parser",
    description: "Parser",
    documentation: "",
  },
  proxy: undefined,
};

beforeEach(() => {
  useAuthStore.setState({ platformWorkflowActions: null });
  mockReadOnly = false;
  jest.clearAllMocks();
});

it("does not mount file inputs without file_read, preserving the existing reference", () => {
  useAuthStore.setState({ platformWorkflowActions: ["read", "update"] });
  render(
    <CustomParameterComponent
      {...props}
      templateData={{ type: "file" }}
      templateValue="own-existing-file.txt"
    />,
  );
  expect(screen.queryByTestId("parser-template")).not.toBeInTheDocument();
  expect(screen.getByText(/个人文件读取权限/)).toBeInTheDocument();
  expect(props.handleOnNewValue).not.toHaveBeenCalled();
});

it("disables the real field after revocation without clearing the retained draft", async () => {
  const view = render(<CustomParameterComponent {...props} />);
  const input = screen.getByTestId("parser-template");
  await userEvent.type(input, " draft");
  expect(input).toHaveValue("saved {text} draft");
  jest.mocked(props.handleOnNewValue).mockClear();
  mockReadOnly = true;
  view.rerender(<CustomParameterComponent {...props} />);
  expect(input).toBeDisabled();
  expect(input).toHaveValue("saved {text} draft");
  await userEvent.keyboard("must-not-edit");
  expect(input).toHaveValue("saved {text} draft");
  expect(props.handleOnNewValue).not.toHaveBeenCalled();
  expect(
    screen.getByRole("button", { name: "Expand text editor" }),
  ).toBeDisabled();
});

it("keeps an initially readonly real input disabled and preserves its saved text", () => {
  mockReadOnly = true;
  render(<CustomParameterComponent {...props} />);
  expect(screen.getByTestId("parser-template")).toBeDisabled();
  expect(screen.getByTestId("parser-template")).toHaveValue("saved {text}");
  expect(props.handleOnNewValue).not.toHaveBeenCalled();
});

it("cannot apply changes from an editor portal opened before revocation", async () => {
  const view = render(<CustomParameterComponent {...props} />);
  await userEvent.click(
    screen.getByRole("button", { name: "Expand text editor" }),
  );
  await userEvent.clear(screen.getByTestId("text-area-modal"));
  await userEvent.type(screen.getByTestId("text-area-modal"), "portal draft");
  mockReadOnly = true;
  view.rerender(<CustomParameterComponent {...props} />);
  await userEvent.click(screen.getByTestId("genericModalBtnSave"));
  expect(screen.getByTestId("parser-template")).toHaveValue("saved {text}");
  expect(props.handleOnNewValue).not.toHaveBeenCalled();
});
