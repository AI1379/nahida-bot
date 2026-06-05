<script setup lang="ts">
import { ExternalLink, BookOpen, Scale, Github, Heart } from "lucide-vue-next";
import Card from "@/components/ui/Card.vue";
</script>

<template>
  <div class="about-page">
    <section class="section">
      <h2 class="section-title">About</h2>
      <Card class="hero-card">
        <div class="hero-content">
          <div class="hero-text">
            <h1 class="hero-title">Nahida Bot</h1>
            <p class="hero-desc">
              A high-performance multi-platform LLM chatbot framework built with
              modular architecture and plugin extensibility in mind.
            </p>
            <p class="hero-desc italic">
              Made with
              <Heart :size="16" class="inline-icon" />
              by
              <a href="https://github.com/AI1379" target="_blank" rel="noopener noreferrer">Renatus Madrigal</a>
            </p>
          </div>
          <img src="/NahidaAvatar3.png" alt="Nahida Bot" class="hero-avatar" />
        </div>
      </Card>
    </section>

    <section class="section">
      <h2 class="section-title">Links</h2>
      <div class="grid grid-3">
        <a href="https://nahida-bot.cobaltdev.top" target="_blank" rel="noopener noreferrer" class="link-card">
          <Card class="link-card-inner">
            <BookOpen :size="28" class="link-icon" />
            <span class="link-label">Documentation</span>
            <span class="link-url">nahida-bot.cobaltdev.top</span>
            <ExternalLink :size="14" class="link-badge" />
          </Card>
        </a>

        <a href="https://github.com/AI1379/nahida-bot" target="_blank" rel="noopener noreferrer" class="link-card">
          <Card class="link-card-inner">
            <Github :size="28" class="link-icon" />
            <span class="link-label">Source Code</span>
            <span class="link-url">github.com/AI1379/nahida-bot</span>
            <ExternalLink :size="14" class="link-badge" />
          </Card>
        </a>

        <a href="https://github.com/AI1379/nahida-bot/blob/main/LICENSE" target="_blank" rel="noopener noreferrer"
          class="link-card">
          <Card class="link-card-inner">
            <Scale :size="28" class="link-icon" />
            <span class="link-label">License</span>
            <span class="link-url">GNU AGPL v3</span>
            <ExternalLink :size="14" class="link-badge" />
          </Card>
        </a>
      </div>
    </section>
  </div>
</template>

<style scoped>
.about-page {
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
  max-width: 720px;
}

.section-title {
  font-size: 0.75rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--color-muted-foreground);
  margin: 0 0 0.5rem;
}

.hero-card {
  padding: 1.25rem;
}

.hero-content {
  display: flex;
  align-items: center;
  gap: 1rem;
}

.hero-avatar {
  width: 160px;
  height: 160px;
  border-radius: var(--radius-lg);
  object-fit: cover;
  flex-shrink: 0;
  box-shadow: 0 2px 12px color-mix(in srgb, var(--color-primary) 15%, transparent);
}

.hero-text {
  flex: 1;
}

.hero-title {
  font-size: 1.5rem;
  font-weight: 700;
  margin: 0 0 0.5rem;
  color: var(--color-foreground);
}

.hero-desc {
  font-size: 1rem;
  color: var(--color-muted-foreground);
  line-height: 1.6;
  margin: 0;
}

.grid {
  display: grid;
  gap: 0.75rem;
}

.grid-3 {
  grid-template-columns: repeat(3, 1fr);
}

@media (max-width: 640px) {
  .hero-content {
    flex-direction: column;
    text-align: center;
  }

  .hero-avatar {
    order: -1;
    width: 120px;
    height: 120px;
    margin-bottom: 0.5rem;
  }

  .grid-3 {
    grid-template-columns: 1fr;
  }
}

.link-card {
  text-decoration: none;
  color: inherit;
  display: block;
}

.link-card:hover .link-card-inner {
  border-color: var(--color-primary);
  box-shadow: 0 0 12px color-mix(in srgb, var(--color-primary) 10%, transparent);
}

.link-card-inner {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  gap: 0.5rem;
  padding: 1.25rem 1rem;
  transition: border-color 0.15s, box-shadow 0.15s;
  height: 100%;
}

.link-icon {
  color: var(--color-primary);
  flex-shrink: 0;
}

.link-label {
  font-size: 0.9375rem;
  font-weight: 600;
  color: var(--color-foreground);
}

.link-url {
  font-size: 0.75rem;
  color: var(--color-muted-foreground);
  font-family: var(--font-mono);
  word-break: break-all;
}

.link-badge {
  position: absolute;
  top: 0.625rem;
  right: 0.625rem;
  color: var(--color-muted-foreground);
  opacity: 0.5;
}

.link-card:hover .link-badge {
  opacity: 1;
  color: var(--color-primary);
}

.inline-icon {
  display: inline-block;
  vertical-align: text-top;
  margin: 0 0.125rem;
  color: var(--color-primary);
}
</style>
