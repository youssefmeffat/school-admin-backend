
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;

namespace Api
{
    public class Program
    {
        public static async Task Main(string[] args)
        {
            var builder = WebApplication.CreateBuilder(args);

            // Add services to the container.

            builder.Services.AddControllers()
                .AddJsonOptions(o =>
                {
                    o.JsonSerializerOptions.ReferenceHandler = System.Text.Json.Serialization.ReferenceHandler.Preserve;
                    o.JsonSerializerOptions.MaxDepth = 64;
                });
            // Register Infrastructure (EF Core + MySQL)
            Infrastructure.Extensions.ServiceCollectionExtensions.AddInfrastructure(builder.Services, builder.Configuration);
            // Learn more about configuring Swagger/OpenAPI at https://aka.ms/aspnetcore/swashbuckle
            builder.Services.AddEndpointsApiExplorer();
            builder.Services.AddSwaggerGen();

            // Register application services
            builder.Services.AddScoped<Application.Services.IStudentExamService, Application.Services.StudentExamService>();
            builder.Services.AddScoped<Application.Services.IReportService, Application.Services.ReportService>();
            builder.Services.AddScoped<Application.Services.ISubjectsService, Application.Services.SubjectsService>();
            builder.Services.AddScoped<Application.Services.ITeachersService, Application.Services.TeachersService>();
            builder.Services.AddScoped<Application.Services.IGradesService, Application.Services.GradesService>();
            builder.Services.AddScoped<Application.Services.IGradeSubjectsService, Application.Services.GradeSubjectsService>();
            builder.Services.AddScoped<Application.Services.IStudentsService, Application.Services.StudentsService>();
            builder.Services.AddScoped<Application.Services.ISchoolClassesService, Application.Services.SchoolClassesService>();
            builder.Services.AddScoped<Application.Services.IExamsService, Application.Services.ExamsService>();
            builder.Services.AddScoped<Application.Services.ITeachingAssignmentsService, Application.Services.TeachingAssignmentsService>();
            builder.Services.AddScoped<Application.Services.IAttendanceService, Application.Services.AttendanceService>();
            builder.Services.AddScoped<Application.Services.IDashboardService, Application.Services.DashboardService>();

            builder.Services.AddCors(options =>
            {
                options.AddPolicy("AngularDev", policy =>
                {
                    policy.WithOrigins("http://localhost:4200")
                        .AllowAnyHeader()
                        .AllowAnyMethod();
                });
            });

            var app = builder.Build();

            // Configure the HTTP request pipeline.
            if (app.Environment.IsDevelopment())
            {
                app.UseSwagger();
                app.UseSwaggerUI();
            }

            app.UseHttpsRedirection();
            app.UseCors("AngularDev");
            app.UseAuthorization();

            // Attempt to apply any pending migrations at startup. This is safe-guarded
            // so the app can still start even if the database isn't reachable (e.g. local dev without MySQL).
            try
            {
                using (var scope = app.Services.CreateScope())
                {
                    var db = scope.ServiceProvider.GetRequiredService<Infrastructure.Data.SchoolDbContext>();

                    // The checked-in migrations are authored against MySQL. Replaying them against
                    // the SQLite local-dev fallback trips a spurious PendingModelChangesWarning
                    // (provider type mappings differ, not the model), so build SQLite's schema
                    // straight from the current model instead.
                    if (db.Database.ProviderName == "Microsoft.EntityFrameworkCore.Sqlite")
                        db.Database.EnsureCreated();
                    else
                        db.Database.Migrate();

                    // Seed sample data
                    await Infrastructure.Data.DbInitializer.SeedAsync(db);
                }
            }
            catch (Exception ex)
            {
                var logger = app.Services.GetService<ILogger<Program>>();
                logger?.LogWarning(ex, "Database migrate at startup failed");
            }

            app.MapControllers();
            app.Run();
        }
    }
}
