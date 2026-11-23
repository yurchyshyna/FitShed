workspace "FitShed" "Система розкладу та онлайн-запису для фітнес-центру" {

  model {
    client  = person "Клієнт" "Записується на заняття, отримує сповіщення"
    trainer = person "Тренер" "Переглядає свій розклад, відмічає відвідуваність"
    admin   = person "Адміністратор" "Складає розклад, керує абонементами, аналізує заповненість"

    pay    = softwareSystem "Платіжний сервіс" "Приймає онлайн-оплату абонементів" "External"
    notify = softwareSystem "Сервіс повідомлень" "Доставляє email і push-сповіщення клієнтам і тренерам" "External"

    fitshed = softwareSystem "FitShed" "Розклад, онлайн-запис і лист очікування, абонементи, відвідуваність і звіти" {
      web    = container "Веб-застосунок" "Розклад і запис для клієнта, кабінет тренера, панель адміністратора" "React SPA, nginx"
      api    = container "Backend API" "Автентифікація, розклад, запис і лист очікування, абонементи, відвідуваність, прийом webhook оплати" "Python, FastAPI"
      worker = container "Фоновий воркер" "Нагадування, сповіщення про зміни, переведення з листа очікування, нічні звіти" "Python"
      db     = container "База даних" "Клієнти, тренери, зали, заняття, записи, абонементи, відвідуваність, черга завдань" "PostgreSQL" "Database"
    }

    client  -> web "Записується, скасовує, оплачує" "HTTPS"
    trainer -> web "Відмічає відвідуваність" "HTTPS"
    admin   -> web "Керує розкладом, дивиться звіти" "HTTPS"
    client  -> pay "Вводить дані картки на сторінці оплати" "HTTPS"

    web    -> api "Викликає REST API" "JSON/HTTPS"
    api    -> db "Читає та записує дані в транзакціях, ставить завдання в чергу" "SQL, TCP 5432"
    api    -> pay "Створює платіж" "REST/JSON, HTTPS"
    pay    -> api "Повідомляє статус оплати" "webhook, HTTPS"
    worker -> db "Бере завдання, оновлює записи, зберігає звіти" "SQL, TCP 5432"
    worker -> notify "Передає сповіщення" "REST/JSON, HTTPS"
  }

  views {
    systemContext fitshed "Context" {
      include *
      autolayout tb
    }

    container fitshed "Containers" {
      include *
      autolayout tb
    }

    styles {
      element "Person" {
        shape Person
        background #0b4f4a
        color #ffffff
      }
      element "Software System" {
        background #1f7a72
        color #ffffff
      }
      element "Container" {
        background #1f7a72
        color #ffffff
      }
      element "External" {
        background #6b8f8b
        color #ffffff
      }
      element "Database" {
        shape Cylinder
        background #4f9d94
        color #ffffff
      }
    }

    theme default
  }
}
