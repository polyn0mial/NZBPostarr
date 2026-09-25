// Settings page methods: servers. Spread into the page's methods by index.js.
export const serversMethods = {
    // ============================================================
    //  Server Management
    // ============================================================
    addServer(event) {
        if (event) event.stopPropagation();

        if (!this.newServer.host || !this.newServer.user || !this.newServer.password) {
            this.showToast('error', 'Missing Info', 'Host, Username, and Password are required');
            return;
        }

        this.servers.push({
            name: this.newServer.name || this.newServer.host,
            host: this.newServer.host,
            port: this.newServer.port,
            user: this.newServer.user,
            password: this.newServer.password,
            max_connections: this.newServer.max_connections,
            ssl: true,
            enabled: true,
        });

        // Reset new server form
        this.newServer = {
            name: '',
            host: '',
            port: null,
            user: '',
            password: '',
            max_connections: null,
            ssl: true,
            enabled: true,
        };
    },

    removeServer(index) {
        this.servers.splice(index, 1);
    },
};
