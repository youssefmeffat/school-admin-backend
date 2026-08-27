using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;

namespace Infrastructure.Extensions
{
    public static class ServiceCollectionExtensions
    {
        public static IServiceCollection AddInfrastructure(this IServiceCollection services, IConfiguration configuration)
        {
            // Prefer MySQL if a DefaultConnection is provided; otherwise fall back to SQLite for local dev.
            var mySqlConn = configuration.GetConnectionString("DefaultConnection");
            if (!string.IsNullOrWhiteSpace(mySqlConn))
            {
                // Use Pomelo MySQL provider and auto-detect server version from the connection string.
                services.AddDbContext<Infrastructure.Data.SchoolDbContext>(options =>
                    options.UseMySql(mySqlConn, ServerVersion.AutoDetect(mySqlConn))
                );
            }
            else
            {
                var sqliteConn = configuration.GetConnectionString("SqliteConnection") ?? "Data Source=school.db";
                services.AddDbContext<Infrastructure.Data.SchoolDbContext>(options =>
                    options.UseSqlite(sqliteConn)
                );
            }

            return services;
        }
    }
}
