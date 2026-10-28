// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import type { MouseEvent, ReactNode } from "react";
import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";

interface ModalProps {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
  testId?: string;
  // When false, clicking the backdrop or pressing Escape does NOT close the
  // dialog (use for long forms/wizards to avoid losing input on a misclick).
  // Defaults to false so dialogs only close via the × / cancel buttons.
  dismissable?: boolean;
}

export function Modal({
  open,
  title,
  onClose,
  children,
  testId,
  dismissable = false,
}: ModalProps) {
  // Track where the mouse press started so a drag-select that ends on the
  // backdrop does not count as a backdrop click.
  const pressedOnBackdrop = useRef(false);

  useEffect(() => {
    if (!open || !dismissable) {
      return;
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        onClose();
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, dismissable, onClose]);

  if (!open) {
    return null;
  }

  function onBackdropMouseDown(event: MouseEvent<HTMLDivElement>) {
    pressedOnBackdrop.current = event.target === event.currentTarget;
  }

  function onBackdropMouseUp(event: MouseEvent<HTMLDivElement>) {
    const isBackdrop = event.target === event.currentTarget;
    if (dismissable && isBackdrop && pressedOnBackdrop.current) {
      onClose();
    }
    pressedOnBackdrop.current = false;
  }

  // Render through a portal to document.body so the fixed-position overlay is
  // never trapped inside an ancestor that establishes a containing block for
  // fixed descendants (e.g. the header's backdrop-filter). Without this, a
  // modal opened from the header (notification bell) is clipped to the header
  // and covered by the page body.
  return createPortal(
    <div
      className="modal-overlay"
      role="presentation"
      onMouseDown={onBackdropMouseDown}
      onMouseUp={onBackdropMouseUp}
    >
      <div
        className="modal-dialog"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        data-testid={testId}
      >
        <div className="modal-header">
          <h2 className="modal-title">{title}</h2>
          <button
            type="button"
            className="modal-close"
            aria-label="关闭"
            onClick={onClose}
            data-testid="modal-close-button"
          >
            ×
          </button>
        </div>
        <div className="modal-body">{children}</div>
      </div>
    </div>,
    document.body,
  );
}
