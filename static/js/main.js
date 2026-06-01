const INDIA_CITY_STATE = {
  "Ahmedabad": "Gujarat",
  "Ajmer": "Rajasthan",
  "Amritsar": "Punjab",
  "Aurangabad": "Maharashtra",
  "Bengaluru": "Karnataka",
  "Bhopal": "Madhya Pradesh",
  "Bhubaneswar": "Odisha",
  "Chandigarh": "Chandigarh",
  "Chennai": "Tamil Nadu",
  "Coimbatore": "Tamil Nadu",
  "Dehradun": "Uttarakhand",
  "Delhi": "Delhi",
  "Faridabad": "Haryana",
  "Ghaziabad": "Uttar Pradesh",
  "Gurugram": "Haryana",
  "Guwahati": "Assam",
  "Hyderabad": "Telangana",
  "Indore": "Madhya Pradesh",
  "Jaipur": "Rajasthan",
  "Jamshedpur": "Jharkhand",
  "Jodhpur": "Rajasthan",
  "Kanpur": "Uttar Pradesh",
  "Kochi": "Kerala",
  "Kolkata": "West Bengal",
  "Kozhikode": "Kerala",
  "Lucknow": "Uttar Pradesh",
  "Ludhiana": "Punjab",
  "Madurai": "Tamil Nadu",
  "Mangaluru": "Karnataka",
  "Mumbai": "Maharashtra",
  "Mysuru": "Karnataka",
  "Nagpur": "Maharashtra",
  "Nashik": "Maharashtra",
  "Noida": "Uttar Pradesh",
  "Patna": "Bihar",
  "Pune": "Maharashtra",
  "Raipur": "Chhattisgarh",
  "Rajkot": "Gujarat",
  "Ranchi": "Jharkhand",
  "Surat": "Gujarat",
  "Thiruvananthapuram": "Kerala",
  "Udaipur": "Rajasthan",
  "Vadodara": "Gujarat",
  "Varanasi": "Uttar Pradesh",
  "Vijayawada": "Andhra Pradesh",
  "Visakhapatnam": "Andhra Pradesh",
};

const INDIA_CITIES = Object.keys(INDIA_CITY_STATE).sort();
const INDIA_STATES = Array.from(
  new Set(Object.values(INDIA_CITY_STATE))
).sort();

function populateSelect(select, options, placeholder) {
  if (!select) return;
  const selected = select.dataset.selected || select.value || "";
  if (select.dataset.populated === "1") {
    if (selected) {
      select.value = selected;
    }
    return;
  }
  select.innerHTML = "";
  const placeholderOption = document.createElement("option");
  placeholderOption.value = "";
  placeholderOption.textContent = placeholder;
  select.appendChild(placeholderOption);
  options.forEach((opt) => {
    const option = document.createElement("option");
    option.value = opt;
    option.textContent = opt;
    select.appendChild(option);
  });
  if (selected) {
    select.value = selected;
  }
  select.dataset.populated = "1";
}

function findStateSelect(citySelect) {
  const targetId = citySelect.dataset.stateTarget;
  if (targetId) {
    return document.getElementById(targetId);
  }
  const row = citySelect.closest(".js-city-state-row");
  if (row) {
    return row.querySelector(".js-state-select");
  }
  return null;
}

function initIndiaCityState(root = document) {
  const citySelects = root.querySelectorAll("select.js-city-select");
  const stateSelects = root.querySelectorAll("select.js-state-select");

  stateSelects.forEach((select) => {
    populateSelect(select, INDIA_STATES, "— Select State —");
  });

  citySelects.forEach((select) => {
    populateSelect(select, INDIA_CITIES, "— Select City —");
    const stateSelect = findStateSelect(select);
    const selectedCity = select.dataset.selected || select.value || "";
    if (selectedCity && stateSelect) {
      const state = INDIA_CITY_STATE[selectedCity];
      if (state) {
        stateSelect.value = state;
      }
    }
    select.addEventListener("change", function () {
      const state = INDIA_CITY_STATE[select.value];
      if (state && stateSelect) {
        stateSelect.value = state;
      }
    });
  });
}

window.initIndiaCityState = initIndiaCityState;

document.addEventListener("DOMContentLoaded", function () {
  const toggleBtn = document.getElementById("sidebarToggle");
  const sidebar = document.getElementById("sidebar");

  if (toggleBtn && sidebar) {
    toggleBtn.addEventListener("click", function () {
      sidebar.classList.toggle("si-sidebar-open");
    });
  }

  // Close sidebar on outside click (mobile)
  document.addEventListener("click", function (e) {
    if (!sidebar || !toggleBtn) return;
    const clickedInside = sidebar.contains(e.target) || toggleBtn.contains(e.target);
    if (!clickedInside && window.innerWidth < 992) {
      sidebar.classList.remove("si-sidebar-open");
    }
  });

  initIndiaCityState();

  document.addEventListener("click", function (e) {
    const row = e.target.closest(".js-row-link");
    if (!row) return;
    if (e.target.closest("a, button, input, select, textarea, label")) {
      return;
    }
    const href = row.dataset.href;
    if (href) {
      window.location.href = href;
    }
  });

  const phoneRevealTimers = new Map();
  let lastPhoneRevealAt = 0;
  let lastPhoneCandidateId = null;

  function getPhoneTarget(button) {
    const container = button.parentElement;
    if (!container) return null;
    return container.querySelector(".js-phone-value, .js-phone-input");
  }

  function setPhoneValue(target, value) {
    if (!target) return;
    if (target.tagName.toLowerCase() === "input") {
      target.value = value;
    } else {
      target.textContent = value;
    }
  }

  function getMaskValue(target) {
    if (!target) return "";
    return target.dataset.mask || "";
  }

  async function revealPhone(button) {
    const candidateId = button.dataset.candidateId;
    if (!candidateId) return;

    const target = getPhoneTarget(button);
    if (!target) return;

    button.disabled = true;
    try {
      const response = await fetch(`/candidates/${candidateId}/phone/reveal`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({})
      });
      const data = await response.json();
      if (!data || !data.ok) {
        return;
      }

      setPhoneValue(target, data.phone);
      lastPhoneRevealAt = Date.now();
      lastPhoneCandidateId = candidateId;

      const existing = phoneRevealTimers.get(target);
      if (existing) {
        clearTimeout(existing);
      }

      const maskValue = data.masked || getMaskValue(target);
      const timeoutId = setTimeout(() => {
        const currentMask = getMaskValue(target) || maskValue;
        setPhoneValue(target, currentMask);
        phoneRevealTimers.delete(target);
      }, (data.expires_in || 10) * 1000);
      phoneRevealTimers.set(target, timeoutId);
    } finally {
      button.disabled = false;
    }
  }

  document.querySelectorAll(".js-reveal-phone").forEach((button) => {
    if (button.dataset.bound === "1") return;
    button.dataset.bound = "1";
    button.addEventListener("click", function () {
      revealPhone(button);
    });
  });

  async function logScreenshotAttempt(reason) {
    const context = document.getElementById("candidatePhoneContext");
    const candidateId = context ? context.dataset.candidateId : lastPhoneCandidateId;
    try {
      await fetch("/candidates/phone/screenshot-log", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ candidate_id: candidateId, reason })
      });
    } catch (err) {
      // Best-effort logging only.
    }
  }

  document.addEventListener("keydown", function (e) {
    if (e.key === "PrintScreen") {
      logScreenshotAttempt("printscreen_key");
    }
    if (e.key && e.key.toLowerCase() === "s" && e.metaKey && e.shiftKey) {
      logScreenshotAttempt("system_screenshot_shortcut");
    }
  });

  document.addEventListener("visibilitychange", function () {
    if (document.hidden && lastPhoneRevealAt) {
      const delta = Date.now() - lastPhoneRevealAt;
      if (delta <= 10000) {
        logScreenshotAttempt("visibility_change_after_reveal");
      }
    }
  });
});
