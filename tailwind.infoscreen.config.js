module.exports = {
    darkMode: "class",
    content: ["./app/templates/weather/**/*.html", "./app/templates/infoscreen/statistik.html"],
    plugins: [require("@tailwindcss/forms"), require("@tailwindcss/container-queries")],
    theme: {
      extend: {
        colors: {
          "background":                "#0b1326",
          "surface":                   "#0b1326",
          "surface-container-lowest":  "#060e20",
          "surface-container-low":     "#131b2e",
          "surface-container":         "#171f33",
          "surface-container-high":    "#222a3d",
          "surface-container-highest": "#2d3449",
          "on-surface":                "#dae2fd",
          "on-surface-variant":        "#e4beba",
          "primary":                   "#ffb3ac",
          "primary-container":         "#d32f2f",
          "on-primary-container":      "#fff2f0",
          "secondary":                 "#bcc7de",
          "tertiary":                  "#adc6ff",
          "error":                     "#ffb4ab",
          "error-container":           "#93000a",
          "on-error-container":        "#ffdad6",
          "outline":                   "#ab8985",
          "outline-variant":           "#5b403d"
        },
        borderRadius: { DEFAULT:"0.125rem", lg:"0.25rem", xl:"0.5rem", full:"0.75rem" },
        fontFamily: { sans:["Hanken Grotesk","sans-serif"], mono:["JetBrains Mono","monospace"] },
        fontSize: {
          "label-caps": ["12px",{lineHeight:"16px",letterSpacing:"0.05em",fontWeight:"600"}],
          "body-sm":    ["14px",{lineHeight:"20px"}],
          "headline-sm":["16px",{lineHeight:"24px",fontWeight:"600"}],
          "headline-md":["20px",{lineHeight:"28px",fontWeight:"600"}]
        }
      }
    }
  };
