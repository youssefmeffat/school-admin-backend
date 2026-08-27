using System.Collections.Generic;

namespace Core.Models
{
    public class School
    {
        public int Id { get; set; }
        public string? Name { get; set; }

        public List<Class> Classes { get; set; } = new();
    }
}
