"""Compact offline Windows localization installer. Never launches the game."""
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from windows_installer import Installer, default_data_dir


class Window:
    def __init__(self, root):
        self.root = root
        self.installer = Installer(Path(__file__).resolve().parent, default_data_dir())
        self.events = queue.Queue()
        self.busy = False
        root.title('GemCraft — русификация (private preview)')
        root.geometry('650x390')
        self.path = tk.StringVar()

        frame = ttk.Frame(root, padding=16)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='Папка игры или gc-cs-steam.swf').pack(anchor='w')
        ttk.Entry(frame, textvariable=self.path).pack(fill='x', pady=8)
        row = ttk.Frame(frame)
        row.pack(fill='x')
        self.buttons = []
        for label, callback in [('Выбрать папку', self.folder), ('Выбрать файл', self.file),
                                ('Проверить', self.inspect)]:
            self.button(row, label, callback)
        self.details = ScrolledText(frame, height=10, wrap='word', font=('Segoe UI', 10))
        self.details.pack(fill='both', expand=True, pady=14)
        self.show_status(f'Версия установщика: {self.installer.version}\nВыберите игру.')
        row = ttk.Frame(frame)
        row.pack(fill='x')
        self.button(row, 'Установить / обновить', lambda: self.confirm('install'))
        self.button(row, 'Восстановить оригинал', lambda: self.confirm('restore'))
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.after(100, self.poll)

    def show_status(self, text):
        self.details.configure(state='normal')
        self.details.delete('1.0', 'end')
        self.details.insert('1.0', text)
        self.details.configure(state='disabled')

    def button(self, parent, label, callback):
        button = ttk.Button(parent, text=label, command=callback)
        button.pack(side='left', padx=3)
        self.buttons.append(button)

    def folder(self):
        value = filedialog.askdirectory(title='Папка GemCraft: Chasing Shadows')
        if value:
            self.path.set(value)
            self.inspect()

    def file(self):
        value = filedialog.askopenfilename(title='gc-cs-steam.swf', filetypes=[('SWF', '*.swf')])
        if value:
            self.path.set(value)
            self.inspect()

    def run(self, callback):
        self.busy = True
        for button in self.buttons:
            button.state(['disabled'])
        self.show_status('Проверка / сборка… Дождитесь завершения.')

        def work():
            try:
                self.events.put(('result', callback()))
            except Exception as error:
                self.events.put(('error', str(error)))
        threading.Thread(target=work, daemon=True).start()

    def inspect(self):
        target = self.path.get()
        self.run(lambda: self.installer.inspect(target))

    def confirm(self, action):
        try:
            target = self.path.get()
            state = self.installer.inspect(target)
            text = ('Установить / обновить русификацию' if action == 'install'
                    else 'Восстановить оригинальный SWF')
            if messagebox.askyesno('Подтвердите операцию',
                                  f'{text}?\n\nЦель: {state["target"]}\nSHA-256: {state["sha256"]}'
                                  f'\nВерсия установщика: {self.installer.version}'
                                  '\n\nСохранения не откатываются. Закройте игру.'):
                self.run(lambda: self.installer.perform(target, action, state['sha256']))
        except Exception as error:
            messagebox.showerror('Ошибка проверки', str(error))

    def poll(self):
        try:
            kind, result = self.events.get_nowait()
        except queue.Empty:
            pass
        else:
            self.busy = False
            for button in self.buttons:
                button.state(['!disabled'])
            if kind == 'error':
                self.show_status(f'Операция остановлена: {result}')
                messagebox.showerror('Ошибка', result)
            else:
                labels = {'original': 'Оригинал', 'localized': 'Известная русификация',
                          'unknown': 'Неизвестный файл'}
                titles = {'state': 'Состояние', 'sha256': 'SHA-256', 'version': 'Версия',
                          'target': 'Файл игры', 'backup': 'Каталог backup и журналов', 'manifest': 'Manifest установки'}
                self.show_status('\n'.join(f'{titles.get(key, key)}: {labels.get(str(value), value)}'
                                          for key, value in result.items()))
        self.root.after(100, self.poll)

    def close(self):
        if self.busy:
            messagebox.showinfo('Операция выполняется', 'Дождитесь завершения операции.')
        else:
            self.root.destroy()


def main():
    root = tk.Tk()
    try:
        Window(root)
    except Exception as error:
        root.withdraw()
        messagebox.showerror('Ошибка установщика', str(error))
        root.destroy()
        return
    root.mainloop()


if __name__ == '__main__':
    main()
