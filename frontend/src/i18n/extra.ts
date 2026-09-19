/**
 * Extra translation keys contributed by the 9 parallel module agents
 * (Guanmai MVP feature polish). Kept in a separate file so module work never
 * has to edit the large shared en.ts/zh.ts. Merged into the base resources
 * in ./index.tsx via deepMerge.
 */
const extra = {
  common: {
    // A submit button that the server will refuse for a reason the form could
    // have known. Shown next to the disabled button, not as a toast.
    noLines: "Add at least one line before submitting.",
    view: "View",
    adjust: "Adjust",
    generate: "Generate",
    preview: "Preview",
  },
  status: {
    intake: {
      // A HUMAN refused this extraction. Deliberately not folded into `failed`
      // (the machine never got that far) or `parked` (an automated guess) — the
      // difference is who decided, and that decides whether it needs a look.
      rejected: "Rejected",
    },
  },
  // Server-configuration warnings. Every one of these is a fact the SERVER
  // already reports on /api/health; showing them here is the difference between
  // "a flag exists" and "somebody will see it". Each sentence ends where the
  // variable name goes — the component appends it, so the names stay in one
  // place and cannot drift from the code that reads them.
  configWarnings: {
    title: "{{count}} server configuration problem(s) — some features are switched off",
    gatewayUrlNotBuiltIn:
      "This build was compiled without a WeCom gateway address, so every WeCom screen is calling a local address no user can reach. Set",
    loopbackGateway:
      "Customer notifications are being sent to the ERP's own container, so no customer is ever texted and nothing errors. Set",
    simulatedOcr:
      "Photos and scanned PDFs are not being read — the extractor returns demo lines instead. Set",
    defaultServiceKey:
      "The intake service key is still the value published in this repository. Rotate",
    defaultGatewayKey: "The ERP-to-gateway secret is still the published default. Rotate",
    notifyOff: "Outbound customer notifications are switched off. Turn on",
  },
  pages: {
    dashboard: {
      needsAttention: "Needs your attention",
      waitingReview: "{{count}} order(s) waiting for review",
      waitingConfirm: "{{count}} order(s) waiting for confirmation",
      nothingWaiting: "Nothing is waiting — every order has been checked.",
      // Not "needs attention" — parked messages need no action, they just
      // must not be invisible.
      parkedInfo: "{{count}} message(s) parked as non-orders and hidden from the inbox",
      reviewParked: "Check them",
    },
    sales: {
      quotationPreview: {
        product: "Product",
        qty: "Qty",
        unit: "Unit",
        unitPrice: "Unit Price",
        amount: "Amount",
        serviceTime: "Service Time",
        pricingCycle: "Pricing Cycle",
      },
    },
    master: {
      customers: {
        orderHistory: "Order history",
        // The API requires name_zh (`CustomerCreate`); the form did not, so it
        // submitted happily and the backend answered with a bare 422.
        nameZhRequired: "Chinese name is required",
      },
      products: {
        viewProduct: "View Product",
        notFound: "Product not found",
      },
      wholesalers: {
        productsSupplied: "Products supplied",
        noProductsLinked: "No products linked.",
        cost: "Cost",
      },
    },
    purchaseOrders: {
      poReceive: {
        receiptRecorded: "Receipt recorded",
        receiveNow: "Receive now",
        recordGoodsReceipt: "Record goods receipt",
        poNumber: "PO {{po_number}}",
        noLines: "This purchase order has no lines.",
      },
    },
    orders: {
      // Gate 3: the order exists, but nobody has confirmed it yet.
      awaitingBanner: "{{count}} order(s) waiting for confirmation",
      awaitingHint:
        "Bold rows need a person to confirm them. No order is sent or processed until then.",
      awaitingConfirmTitle: "This order is not confirmed yet",
      awaitingConfirmBody:
        "Nothing happens automatically from here. Check the lines below, then confirm to send this order on.",
      awaitingConfirmNoPermission:
        "Nothing happens automatically from here. Someone with ops or admin rights has to confirm it.",
      orderTimeline: {
        draft: "Order created",
        pendingConfirmation: "Pending confirmation",
        confirmed: "Confirmed",
        consolidated: "Consolidated",
        outForDelivery: "Out for delivery",
        fulfilled: "Fulfilled",
        invoiced: "Invoiced",
        needsClarification: "Needs clarification",
        rejected: "This order was rejected. Nothing further will happen to it.",
        noDeliveryRecord: "No delivery was recorded for this order.",
      },
    },
    intake: {
      // The "unread" queue: orders parked until a human confirms them.
      pendingBanner: "{{count}} order(s) waiting for review",
      pendingOnly: "Pending only",
      showAll: "Show all",
      unreviewed: "Unreviewed",
      waitingSince: "Waiting since {{time}}",
      reviewHint: "Bold rows still need a person to check them before an order is created.",
      // Parked messages (greetings, "收到", stickers) are hidden from the
      // inbox by default. They must stay reachable: a wrongly-parked message
      // is a lost order, so surface the count and offer a way back.
      parkedBanner: "{{count}} message(s) parked as non-orders",
      parkedOnly: "Parked only",
      parkedHint:
        "These looked like chatter, not orders. If one is actually an order, promote it back into the queue.",
      promote: "Promote",
      promoteConfirm: "Put this message back in the intake queue?",
      promoteSuccess: "Message promoted back to the queue",
      review: {
        // Every order now stops for review — not just the low-confidence ones —
        // so the drawer must not claim the parse was uncertain when it wasn't.
        verifyTitle: "Check this before it becomes an order",
        verifyBody:
          "No order exists yet. Compare the lines below against the original, then confirm.",
        // --- Seeing the source --------------------------------------------
        // "No preview image for this source type" used to be shown for every
        // document, photos included: the source was pointed at with an <img>,
        // which sends no Authorization header, so the request 401'd and the
        // image errored. A message about the source type, caused by auth.
        expand: "Expand",
        collapse: "Collapse",
        expandHint:
          "Widen this screen so the original and the lines can be read together",
        zoomHint: "Click the image to zoom and rotate it",
        openInNewTab: "Open in a new tab",
        noSource: "The original could not be loaded.",
        noSourceText: "No text was stored for this document.",
        noInlinePreview:
          "This file type cannot be shown inline — download it to check the order.",
        previewFailed: "The original could not be shown",
        // --- Correcting the lines -----------------------------------------
        pickProduct: "Pick a product",
        notInCatalog: "not in the catalog",
        unmatched: "No match",
        edited: "Edited",
        editedHint:
          "You changed this line, so the extractor's confidence no longer describes it.",
        customerCancelled: "Cancelled on the note",
        addLine: "Add line",
        removeLine: "Remove this line",
        undoRemove: "Put this line back",
        resetEdits: "Reset my changes",
        addedLine: "The added line",
        lineNo: "Line {{line}}",
        nothingToOrder: "Every line has been removed — there is nothing to order.",
        productRequired: "{{where}} has no product.",
        qtyRequired:
          "{{where}} has a quantity of 0. Give it a quantity, or remove the line.",
        editsApplied:
          "Your changes are sent with the confirm. The original extraction is kept exactly as the machine read it.",
        // --- Refusing it ---------------------------------------------------
        reject: "Reject",
        rejectTitle: "Reject this order",
        rejectBody:
          "No order will be created. The message and the extracted lines are both kept, so this decision can be checked later.",
        rejectReasonLabel: "Reason",
        rejectNote: "Note",
        rejectNotePlaceholder:
          "What happened? This is what the next person will read.",
        rejectNoteRequired: 'A note is required when the reason is "other".',
        rejectConfirm: "Reject order",
        rejected: "Order rejected",
        wasRejected: "Rejected: {{reason}}",
        rejectReason: {
          duplicate: "Duplicate — this order already exists",
          not_an_order: "Not an order",
          wrong_customer: "Wrong customer",
          unreadable: "Cannot be read",
          other: "Other",
        },
      },
      // The binding gate. The server refuses to create an order from an unbound
      // conversation, so the inbox must make that state visible and offer the
      // one action that clears it. A dead "—" in the Customer column is how the
      // operator ends up clicking Confirm and reading a toast that vanishes.
      bind: {
        needsCustomer: "Needs customer",
        unboundOnly: "Needs a customer only",
        pendingNeedsCustomer:
          "{{pending}} order(s) waiting for review · {{unbound}} need a customer first",
        boundTo: "Bound to {{customer}}",
        notBound: "Not bound to a customer",
        notBoundHint:
          "This order cannot be created until the conversation is bound to a customer. One binding releases every order waiting on this conversation.",
        chooseCustomer: "Choose customer",
        bindToContinue: "Bind customer to continue",
        confirmFailed: "Could not create the order",
        unknownSender: "Sender unknown",
      },
    },
    warehouse: {
      inventory: {
        stockAdjust: {
          damage: "Damage",
          count: "Count",
          transfer: "Transfer",
          reason: "Reason",
          quantity: "Quantity",
          selectReason: "Select reason",
          reasonRequired: "Reason is required",
          quantityRequired: "Quantity is required",
          success: "Stock adjusted",
        },
      },
    },
    delivery: {
      delivery: {
        routeZone: "Route / Zone",
        driver: "Assigned Driver",
      },
      // A partial delivery cannot be completed again: the server accepts only
      // picked or out_for_delivery. The button used to be offered anyway.
      partialNoComplete: "Partial — cannot be completed",
      partialHint:
        "This delivery was recorded as partial, so it cannot be completed again. The remainder is delivered on a new delivery.",
    },
    finance: {
      statements: {
        title: "Generate Statement",
        hint: "Generate a customer statement for the selected date range.",
        customer: "Customer",
        validationCustomer: "Please select a customer",
        validationFrom: "Please select a start date",
        to: "To",
        validationTo: "Please select an end date",
        generated: "Statement generated",
      },
    },
    identity: {
      navLabel: "Unbound chats",
      queue: {
        title: "Unbound chats",
        intro: "These conversations are not linked to a customer yet",
        introBody:
          "A conversation is always one customer, but nothing in the system can know which one until a person says so. Orders from an unbound chat are held and cannot become an order. Binding a chat releases everything held for it.",
        colChat: "Conversation",
        colCorp: "Company (from WeCom)",
        colWaiting: "Waiting",
        colLastMessage: "Last message",
        colAction: "Decision",
        unnamed: "Unnamed chat",
        bind: "Bind to a customer",
        empty: "No unbound chats — every conversation has a customer.",
        emptyHint: "New conversations from WeCom appear here on their own.",
        waitingCount: "{{count}} waiting",
        loadError: "Could not load the unbound chats",
      },
      bind: {
        title: "Bind this conversation to a customer",
        back: "Back to unbound chats",
        notFound:
          "This conversation is no longer in the unbound queue. Someone may have just bound it.",
        noChatKey:
          "This conversation carries no WeCom identifier, so it cannot be bound. It has to be traced back to a contact first.",
        leftTitle: "Who is talking",
        rightTitle: "Who you are binding to",
        identityName: "Display name",
        alias: "Alias / remark",
        corp: "Company",
        phone: "Phone",
        unknown: "not provided by WeCom",
        chatKey: "Chat key",
        chatKeyHint:
          "The only thing a binding is made on. Display names are editable and change without notice.",
        firstSeen: "First seen",
        firstSeenHint: "earliest held document — earlier messages may exist",
        waiting: "Messages waiting",
        heldTitle: "Held documents ({{count}})",
        heldHint:
          "Nothing here has become an order. These are released to the customer the moment you bind.",
        noHeld: "No held documents.",
        selectDocument: "Document",
        sourceType: "Source",
        viewOriginal: "View original",
        hideOriginal: "Hide original",
        originalUnavailable: "This document has no original file.",
        extractedText: "Extracted text",
        noExtractedText: "No text was extracted from this document.",
        documentLoadError: "Could not load this document.",
        proposalTitle: "Company details proposed by extraction",
        proposalHint:
          "Read-only. These values were matched from the document by pattern — they are evidence to check, not facts.",
        extractedNotVerified: "extracted — not verified",
        evidence: "Evidence",
        noEvidence: "no source line found",
        method: "Method",
        fieldNotFound: "not found",
        rawExcerpt: "Text the extraction read",
        searchLabel: "Find a customer",
        searchPlaceholder: "Search by code, name, phone or delivery zone",
        searchHint: "Nothing is pre-selected. A pre-filled search is a suggestion, not a choice.",
        searchFromChat: "Pre-filled from this conversation: {{name}}",
        searchFromChatHint: "Nothing is selected. Clear the box to see every customer.",
        matchedOn: "matched on {{field}}",
        resultsCount: "{{count}} match(es)",
        noResults: "No customer matches that search.",
        showingFirst:
          "Showing the first {{count}} customers — type to search across all of them.",
        selectedTitle: "Selected customer",
        selectedHint:
          "Check this is the right account before binding. Both sides have to agree.",
        noneSelected: "No customer selected yet",
        noneSelectedHint:
          "Search on the right and choose one. The bind button stays disabled until you do.",
        code: "Code",
        name: "Name",
        zone: "Delivery zone",
        status: "Status",
        contact: "Contact",
        address: "Address",
        addressUnverified: "address never verified",
        recentOrders: "Last 3 orders",
        recentOrdersEmpty:
          "This customer has no orders yet — check carefully that it is the right account.",
        recentOrdersHint:
          "A sanity check in both directions: do these look like the orders this conversation would place?",
        orderLines: "Products",
        createNew: "This is a new customer — create one",
        createNewHint:
          "Use this when the conversation belongs to a company that is not in the list yet.",
        newCustomerTitle: "New customer",
        newCustomerHint:
          "Fields tagged “{{tag}}” came from the document. Check each one against the original before saving — nothing is verified until you say so.",
        fieldCode: "Customer code",
        fieldNameEn: "Name (English)",
        fieldNameZh: "Name (Chinese)",
        fieldType: "Type",
        fieldAddress: "Delivery address",
        fieldPhone: "Phone",
        fieldContact: "Contact",
        fieldZone: "Delivery zone",
        taxId: "Tax ID",
        requiredFromHuman: "you must type this — it is never extracted",
        confirmChecked:
          "I have checked every pre-filled field above against the original document.",
        confirmCheckedRequired: "Tick the confirmation before creating the customer.",
        fillRequired: "Fill the required fields before creating the customer.",
        createSuccess: "Customer created",
        bindAction: "Bind this conversation",
        bindConfirmTitle: "Bind {{chat}} to {{customer}}?",
        bindConfirmBody:
          "Every held document from this conversation is released to this customer and can become an order. The decision is recorded against your name.",
        bindSuccess: "Bound. {{count}} held document(s) released.",
        bindFailed: "The bind was refused",
        releasesHint:
          "One binding releases all {{count}} order(s) held for this conversation.",
        pageHint:
          "Binding this conversation lets its held orders become orders. Nothing is bound automatically and no match is pre-selected.",
        safetyNote:
          "Nothing is bound automatically and no match is pre-selected. A binding is reversible, and reversing it lists every order that used it.",
      },
      delivery: {
        title: "Delivery confirmation",
        unconfirmed: "Not confirmed",
        unconfirmedHint:
          "This address is only a proposal — it was copied from the customer record and nobody has checked it. The order cannot be confirmed until a person confirms where it is going.",
        confirmed: "Confirmed",
        confirmedAt: "Confirmed {{at}}",
        prefilledWarning: "Pre-filled from the customer record — not checked",
        noAddress:
          "The customer record has no address. Type the delivery address.",
        address: "Delivery address",
        addressPlaceholder: "Building, street, district, city",
        contactName: "Contact name",
        contactPhone: "Contact phone",
        addressRequired: "Enter the delivery address before confirming.",
        confirmAction: "Confirm delivery address",
        reConfirmAction: "Update and re-confirm",
        confirmSuccess: "Delivery confirmed",
        blockedHint: "The order cannot be confirmed until this is done.",
        editHint:
          "Edit the address if it is wrong, then confirm. Your name and the time are recorded with the confirmation.",
      },
    },
  },
};

export default extra;
